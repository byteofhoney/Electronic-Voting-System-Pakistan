from flask import Blueprint, render_template, session, redirect, url_for, request, flash, current_app, jsonify
from database.db import get_db
from functools import wraps
import os
import secrets
import string
import hashlib
from werkzeug.utils import secure_filename
from datetime import datetime, date, time as dtime

import csv, io
from blueprints.voter.cnic_utils import get_province_from_cnic, validate_cnic_format


#audit_trail
from flask import Response


ecp_bp = Blueprint('ecp', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# ── Helpers ────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def ecp_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'ECP':
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

def sync_election_status(cursor):
    """
    Called on every dashboard load.
    Checks current time against schedule and updates status if needed.
    Returns the updated election row or None.
    """
    cursor.execute("""
        SELECT e.election_id, e.election_name, e.status,
               e.election_date,
               es.voting_start_time, es.voting_end_time
        FROM elections e
        LEFT JOIN election_schedule es ON e.election_id = es.election_id
        ORDER BY e.created_at DESC LIMIT 1
    """)
    election = cursor.fetchone()

    if not election:
        return None

    # Only auto-transition Upcoming → Active → Closed
    if election['status'] == 'Closed':
        return election

    if election['voting_start_time'] is None:
        return election

    # Build full datetime objects from date + time parts
    # MySQL returns timedelta for TIME columns — convert to time
    from datetime import datetime, timedelta

    def td_to_time(td):
        """Convert timedelta (from MySQL TIME) to a time object."""
        if isinstance(td, timedelta):
            total_seconds = int(td.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            from datetime import time as dtime
            return dtime(hours, minutes, seconds)
        return td  # already a time object

    election_date  = election['election_date']
    start_time     = td_to_time(election['voting_start_time'])
    end_time       = td_to_time(election['voting_end_time'])

    # Combine date + time
    start_dt = datetime.combine(election_date, start_time)
    end_dt   = datetime.combine(election_date, end_time)
    now      = datetime.now()

    eid = election['election_id']

    if election['status'] == 'Upcoming' and now >= start_dt:
        cursor.execute("""
            UPDATE elections
            SET status = 'Active', opened_at = NOW()
            WHERE election_id = %s
        """, (eid,))
        election['status'] = 'Active'

    elif election['status'] == 'Active' and now >= end_dt:
        cursor.execute("""
            UPDATE elections
            SET status = 'Closed', closed_at = NOW()
            WHERE election_id = %s
        """, (eid,))
        election['status'] = 'Closed'

    return election


# ── Dashboard ──────────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/dashboard')
@ecp_required
def dashboard():
    from datetime import datetime, timedelta

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Auto-sync election status on every load
    election = sync_election_status(cursor)
    db.commit()

    cursor.execute("SELECT COUNT(*) AS total FROM voters")
    total_voters = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM candidates")
    total_candidates = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM polling_stations")
    total_stations = cursor.fetchone()['total']

    cursor.execute("SELECT * FROM audit_trail ORDER BY performed_at DESC LIMIT 5")
    audit_logs = cursor.fetchall()

    # Build schedule info for countdown
    schedule = None
    if election:
        cursor.execute("""
            SELECT * FROM election_schedule
            WHERE election_id = %s LIMIT 1
        """, (election['election_id'],))
        schedule = cursor.fetchone()

    cursor.close()
    db.close()

    # Build ISO strings for JS countdown
    start_iso = None
    end_iso   = None

    if election and schedule:
        def td_to_time(td):
            if isinstance(td, timedelta):
                total = int(td.total_seconds())
                h, r  = divmod(total, 3600)
                m, s  = divmod(r, 60)
                from datetime import time as dtime
                return dtime(h, m, s)
            return td

        from datetime import datetime
        start_dt = datetime.combine(
            election['election_date'],
            td_to_time(schedule['voting_start_time'])
        )
        end_dt = datetime.combine(
            election['election_date'],
            td_to_time(schedule['voting_end_time'])
        )
        start_iso = start_dt.strftime('%Y-%m-%dT%H:%M:%S')
        end_iso   = end_dt.strftime('%Y-%m-%dT%H:%M:%S')

    election_status = election['status'] if election else 'No Election'

    return render_template('ecp/dashboard.html',
        username=session.get('username'),
        total_voters=total_voters,
        total_candidates=total_candidates,
        total_stations=total_stations,
        election_status=election_status,
        election=election,
        schedule=schedule,
        start_iso=start_iso,
        end_iso=end_iso,
        audit_logs=audit_logs
    )


# ── Create Election ────────────────────────────────────────────────────────
@ecp_bp.route('/ecp/election/create', methods=['POST'])
@ecp_required
def create_election():
    election_name  = request.form['election_name'].strip()
    election_date  = request.form['election_date']
    start_time     = request.form['start_time']
    end_time       = request.form['end_time']

    # Basic validation
    if not all([election_name, election_date, start_time, end_time]):
        flash('All fields are required.', 'error')
        return redirect(url_for('ecp.dashboard'))

    if start_time >= end_time:
        flash('End time must be after start time.', 'error')
        return redirect(url_for('ecp.dashboard'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Block if an active or upcoming election already exists
    cursor.execute("""
        SELECT election_id FROM elections
        WHERE status IN ('Active', 'Upcoming')
    """)
    existing = cursor.fetchone()
    if existing:
        flash('An active or upcoming election already exists. '
              'Close it before creating a new one.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.dashboard'))

    # Determine initial status based on current time
    from datetime import datetime
    now       = datetime.now()
    start_dt  = datetime.strptime(f"{election_date} {start_time}", '%Y-%m-%d %H:%M')
    end_dt    = datetime.strptime(f"{election_date} {end_time}",   '%Y-%m-%d %H:%M')

    if now >= end_dt:
        flash('End time is already in the past. '
              'Please set a future end time.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.dashboard'))

    initial_status = 'Active' if now >= start_dt else 'Upcoming'
    opened_at      = 'NOW()' if initial_status == 'Active' else None

    if initial_status == 'Active':
        cursor.execute("""
            INSERT INTO elections
                (election_name, election_date, status, created_by_ecp, opened_at)
            VALUES (%s, %s, 'Active', %s, NOW())
        """, (election_name, election_date, session.get('user_id')))
    else:
        cursor.execute("""
            INSERT INTO elections
                (election_name, election_date, status, created_by_ecp)
            VALUES (%s, %s, 'Upcoming', %s)
        """, (election_name, election_date, session.get('user_id')))

    db.commit()
    election_id = cursor.lastrowid

    # Insert schedule
    cursor.execute("""
        INSERT INTO election_schedule
            (election_id, voting_start_time, voting_end_time)
        VALUES (%s, %s, %s)
    """, (election_id, start_time, end_time))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f"Created election: {election_name} "
          f"({election_date} {start_time}–{end_time}) "
          f"Status: {initial_status}",
          'elections'))
    db.commit()

    cursor.close()
    db.close()

    flash(f'Election "{election_name}" created. '
          f'Status: {initial_status}.', 'success')
    return redirect(url_for('ecp.dashboard'))


# ── Manual Close Election ──────────────────────────────────────────────────
@ecp_bp.route('/ecp/election/close', methods=['POST'])
@ecp_required
def close_election():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT election_id, election_name FROM elections
        WHERE status IN ('Active', 'Upcoming')
        ORDER BY created_at DESC LIMIT 1
    """)
    election = cursor.fetchone()

    if not election:
        flash('No active election found.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.dashboard'))

    cursor.execute("""
        UPDATE elections
        SET status = 'Closed', closed_at = NOW()
        WHERE election_id = %s
    """, (election['election_id'],))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f"Manually closed election: {election['election_name']}",
          'elections'))
    db.commit()

    cursor.close()
    db.close()

    flash(f"Election \"{election['election_name']}\" has been closed.", 'success')
    return redirect(url_for('ecp.dashboard'))


#======

# ── Parties ────────────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/parties')
@ecp_required
def parties():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM parties ORDER BY created_at DESC")
    all_parties = cursor.fetchall()

    for p in all_parties:
        if isinstance(p['created_at'], str):
            p['created_at'] = datetime.strptime(p['created_at'], '%Y-%m-%d %H:%M:%S')

    cursor.close()
    db.close()
    return render_template('ecp/parties.html',
        username=session.get('username'),
        parties=all_parties
    )


@ecp_bp.route('/ecp/parties/add', methods=['POST'])
@ecp_required
def add_party():
    party_name = request.form['party_name']
    abbreviation = request.form['abbreviation']
    logo_path = None

    if 'logo' in request.files:
        file = request.files['logo']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            logo_path = filename

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM parties WHERE party_name=%s OR abbreviation=%s",
                   (party_name, abbreviation))
    existing = cursor.fetchone()

    if existing:
        cursor.close()
        db.close()
        flash('Party name or abbreviation already exists.', 'error')
        return redirect(url_for('ecp.parties'))

    cursor.execute("""
        INSERT INTO parties (party_name, abbreviation, logo_path, created_by_ecp)
        VALUES (%s, %s, %s, %s)
    """, (party_name, abbreviation, logo_path, session.get('user_id')))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f'Added new party: {party_name} ({abbreviation})', 'parties'))
    db.commit()

    cursor.close()
    db.close()
    flash('Party added successfully.', 'success')
    return redirect(url_for('ecp.parties'))


@ecp_bp.route('/ecp/parties/delete/<int:party_id>', methods=['POST'])
@ecp_required
def delete_party(party_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM candidates WHERE party_id=%s", (party_id,))
    linked = cursor.fetchone()
    if linked:
        flash('Cannot delete — this party has candidates linked to it.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.parties'))

    cursor.execute("SELECT party_name FROM parties WHERE party_id=%s", (party_id,))
    party = cursor.fetchone()

    cursor.execute("DELETE FROM parties WHERE party_id=%s", (party_id,))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f"Deleted party: {party['party_name']}", 'parties'))
    db.commit()

    cursor.close()
    db.close()
    flash('Party deleted.', 'success')
    return redirect(url_for('ecp.parties'))

# ── Officers ───────────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/officers')
@ecp_required
def officers():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Only provinces that can have officers (no Islamabad)
    cursor.execute("""
        SELECT p.province_id, p.province_name,
               po.officer_id, po.full_name, po.username,
               po.is_active, po.created_at, po.last_login
        FROM provinces p
        LEFT JOIN provincial_officers po ON p.province_id = po.province_id
        WHERE p.is_federal_territory = 0
        ORDER BY p.province_id
    """)
    province_rows = cursor.fetchall()

    # Build province cards with stats
    province_cards = []
    for row in province_rows:
        card = {
            'province_id':   row['province_id'],
            'province_name': row['province_name'],
            'officer_id':    row['officer_id'],
            'officer_name':  row['full_name'],
            'username':      row['username'],
            'is_active':     row['is_active'],
            'last_login':    row['last_login'],
            'created_at':    row['created_at'],
            'voters':        0,
            'candidates':    0,
            'stations':      0,
            'last_action':   None,
        }

        if row['officer_id']:

            
            # Voters registered by this officer
            cursor.execute("""
                SELECT COUNT(*) AS c FROM voters
                WHERE registered_by = %s
            """, (row['officer_id'],))
            card['voters'] = cursor.fetchone()['c']

            # Candidates added by this officer
            cursor.execute("""
                SELECT COUNT(*) AS c FROM candidates
                WHERE added_by_officer = %s
            """, (row['officer_id'],))
            card['candidates'] = cursor.fetchone()['c']

            

            # Polling stations created by this officer
            cursor.execute("""
                SELECT COUNT(*) AS c FROM polling_stations
                WHERE created_by_officer = %s
            """, (row['officer_id'],))
            card['stations'] = cursor.fetchone()['c']

            # Last action from audit_trail
            cursor.execute("""
                SELECT performed_at, action FROM audit_trail
                WHERE performed_by = %s
                ORDER BY performed_at DESC LIMIT 1
            """, (row['username'],))
            last = cursor.fetchone()
            if last:
                card['last_action'] = last

        province_cards.append(card)

    # All officers list for the table
    cursor.execute("""
        SELECT po.*, p.province_name
        FROM provincial_officers po
        JOIN provinces p ON po.province_id = p.province_id
        ORDER BY po.created_at DESC
    """)
    all_officers = cursor.fetchall()

    # Recent activity feed — last 10 actions by provincial officers
    cursor.execute("""
        SELECT * FROM audit_trail
        WHERE role = 'Provincial Officer'
        ORDER BY performed_at DESC LIMIT 10
    """)
    activity_feed = cursor.fetchall()

    # Provinces for the add form dropdown (only non-federal, no existing active officer)
    cursor.execute("""
        SELECT p.province_id, p.province_name
        FROM provinces p
        WHERE p.is_federal_territory = 0
        AND p.province_id NOT IN (
            SELECT province_id FROM provincial_officers WHERE is_active = 1
        )
        ORDER BY p.province_name
    """)
    available_provinces = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template('ecp/officers.html',
        username=session.get('username'),
        province_cards=province_cards,
        all_officers=all_officers,
        activity_feed=activity_feed,
        available_provinces=available_provinces
    )


@ecp_bp.route('/ecp/officers/add', methods=['POST'])
@ecp_required
def add_officer():
    full_name   = request.form['full_name'].strip()
    username    = request.form['username'].strip()
    password    = request.form['password']
    confirm     = request.form['confirm_password']
    province_id = request.form['province_id']
    email = request.form['email'].strip()

    if password != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('ecp.officers'))

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('ecp.officers'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Check username uniqueness
    cursor.execute("""
        SELECT officer_id FROM provincial_officers WHERE username = %s
    """, (username,))
    if cursor.fetchone():
        flash('Username already exists.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    # Check province does not already have active officer
    cursor.execute("""
        SELECT officer_id FROM provincial_officers
        WHERE province_id = %s AND is_active = 1
    """, (province_id,))
    if cursor.fetchone():
        flash('This province already has an active officer.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    cursor.execute("""
        INSERT INTO provincial_officers
            (full_name, username, password_hash, email, province_id, created_by_ecp)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (full_name, username, hash_password(password), email,
          province_id, session.get('user_id')))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f'Added Provincial Officer: {full_name} ({username})',
          'provincial_officers'))
    db.commit()

    cursor.close()
    db.close()
    flash(f'Officer {full_name} added successfully.', 'success')
    return redirect(url_for('ecp.officers'))


@ecp_bp.route('/ecp/officers/toggle/<int:officer_id>', methods=['POST'])
@ecp_required
def toggle_officer(officer_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username, is_active FROM provincial_officers
        WHERE officer_id = %s
    """, (officer_id,))
    officer = cursor.fetchone()

    if not officer:
        flash('Officer not found.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    new_status = 0 if officer['is_active'] else 1
    action_word = 'Deactivated' if new_status == 0 else 'Activated'

    cursor.execute("""
        UPDATE provincial_officers SET is_active = %s WHERE officer_id = %s
    """, (new_status, officer_id))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f"{action_word} officer: {officer['full_name']} ({officer['username']})",
          'provincial_officers'))
    db.commit()

    cursor.close()
    db.close()
    flash(f"Officer {action_word.lower()} successfully.", 'success')
    return redirect(url_for('ecp.officers'))


@ecp_bp.route('/ecp/officers/reset-password/<int:officer_id>', methods=['POST'])
@ecp_required
def reset_officer_password(officer_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username FROM provincial_officers
        WHERE officer_id = %s
    """, (officer_id,))
    officer = cursor.fetchone()

    if not officer:
        flash('Officer not found.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    new_password = generate_password(10)

    cursor.execute("""
        UPDATE provincial_officers SET password_hash = %s WHERE officer_id = %s
    """, (hash_password(new_password), officer_id))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f"Reset password for officer: {officer['full_name']} ({officer['username']})",
          'provincial_officers'))
    db.commit()

    cursor.close()
    db.close()

    # Show the new password ONCE via flash — ECP must note it down
    flash(f"Password reset for {officer['full_name']}. "
          f"New password: {new_password} — Note this down, it will not be shown again.",
          'password')
    return redirect(url_for('ecp.officers'))


@ecp_bp.route('/ecp/officers/delete/<int:officer_id>', methods=['POST'])
@ecp_required
def delete_officer(officer_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username FROM provincial_officers
        WHERE officer_id = %s
    """, (officer_id,))
    officer = cursor.fetchone()

    if not officer:
        flash('Officer not found.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    # Block delete if officer has linked data
    cursor.execute("SELECT COUNT(*) AS c FROM voters WHERE registered_by = %s",
                   (officer_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — officer has registered voters.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    cursor.execute("SELECT COUNT(*) AS c FROM candidates WHERE added_by_officer = %s",
                   (officer_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — officer has added candidates.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    cursor.execute("SELECT COUNT(*) AS c FROM polling_stations WHERE created_by_officer = %s",
                   (officer_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — officer has created polling stations.', 'error')
        cursor.close()
        db.close()
        return redirect(url_for('ecp.officers'))

    cursor.execute("DELETE FROM provincial_officers WHERE officer_id = %s", (officer_id,))
    db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, %s, %s, %s)
    """, (session.get('username'), 'ECP',
          f"Deleted officer: {officer['full_name']} ({officer['username']})",
          'provincial_officers'))
    db.commit()

    cursor.close()
    db.close()
    flash('Officer deleted.', 'success')
    return redirect(url_for('ecp.officers'))


@ecp_bp.route('/ecp/officers/<int:officer_id>/detail')
@ecp_required
def officer_detail(officer_id):
    """Returns JSON for the officer detail modal."""
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT po.*, p.province_name
        FROM provincial_officers po
        JOIN provinces p ON po.province_id = p.province_id
        WHERE po.officer_id = %s
    """, (officer_id,))
    officer = cursor.fetchone()

    if not officer:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404

    # Stats
    cursor.execute("SELECT COUNT(*) AS c FROM voters WHERE registered_by = %s",
                   (officer_id,))
    voters = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM candidates WHERE added_by_officer = %s",
                   (officer_id,))
    candidates = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM polling_stations WHERE created_by_officer = %s",
                   (officer_id,))
    stations = cursor.fetchone()['c']

    # Recent activity
    cursor.execute("""
        SELECT action, performed_at FROM audit_trail
        WHERE performed_by = %s
        ORDER BY performed_at DESC LIMIT 8
    """, (officer['username'],))
    activity = cursor.fetchall()

    cursor.close()
    db.close()

    # Serialize datetimes for JSON
    def fmt(dt):
        if dt is None:
            return '—'
        if isinstance(dt, str):
            return dt
        return dt.strftime('%d %b %Y, %H:%M')

    return jsonify({
        'officer_id':    officer['officer_id'],
        'full_name':     officer['full_name'],
        'username':      officer['username'],
        'province_name': officer['province_name'],
        'is_active':     officer['is_active'],
        'created_at':    fmt(officer['created_at']),
        'last_login':    fmt(officer['last_login']),
        'voters':        voters,
        'candidates':    candidates,
        'stations':      stations,
        'activity': [
            {
                'action':       a['action'],
                'performed_at': fmt(a['performed_at'])
            }
            for a in activity
        ]
    })


# ── Audit Trail ────────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/audit-trail')
@ecp_required
def audit_trail():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Read filter params from URL
    role_filter   = request.args.get('role', '').strip()
    action_filter = request.args.get('action_type', '').strip()
    date_from     = request.args.get('date_from', '').strip()
    date_to       = request.args.get('date_to', '').strip()

    # Build query dynamically
    query  = "SELECT * FROM audit_trail WHERE 1=1"
    params = []

    if role_filter:
        query += " AND role = %s"
        params.append(role_filter)

    if action_filter:
        query += " AND action LIKE %s"
        params.append(f'%{action_filter}%')

    if date_from:
        query += " AND DATE(performed_at) >= %s"
        params.append(date_from)

    if date_to:
        query += " AND DATE(performed_at) <= %s"
        params.append(date_to)

    query += " ORDER BY performed_at DESC LIMIT 200"

    cursor.execute(query, params)
    logs = cursor.fetchall()

    # Total count without limit
    count_query  = "SELECT COUNT(*) AS total FROM audit_trail WHERE 1=1"
    count_params = []

    if role_filter:
        count_query += " AND role = %s"
        count_params.append(role_filter)

    if action_filter:
        count_query += " AND action LIKE %s"
        count_params.append(f'%{action_filter}%')

    if date_from:
        count_query += " AND DATE(performed_at) >= %s"
        count_params.append(date_from)

    if date_to:
        count_query += " AND DATE(performed_at) <= %s"
        count_params.append(date_to)

    cursor.execute(count_query, count_params)
    total_count = cursor.fetchone()['total']

    cursor.close()
    db.close()

    return render_template('ecp/audit_trail.html',
        username=session.get('username'),
        logs=logs,
        total_count=total_count,
        role_filter=role_filter,
        action_filter=action_filter,
        date_from=date_from,
        date_to=date_to
    )


@ecp_bp.route('/ecp/audit-trail/export')
@ecp_required
def export_audit_csv():
    import csv
    import io

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Same filters from URL — export respects active filters
    role_filter   = request.args.get('role', '').strip()
    action_filter = request.args.get('action_type', '').strip()
    date_from     = request.args.get('date_from', '').strip()
    date_to       = request.args.get('date_to', '').strip()

    query  = "SELECT * FROM audit_trail WHERE 1=1"
    params = []

    if role_filter:
        query += " AND role = %s"
        params.append(role_filter)

    if action_filter:
        query += " AND action LIKE %s"
        params.append(f'%{action_filter}%')

    if date_from:
        query += " AND DATE(performed_at) >= %s"
        params.append(date_from)

    if date_to:
        query += " AND DATE(performed_at) <= %s"
        params.append(date_to)

    query += " ORDER BY performed_at DESC"

    cursor.execute(query, params)
    logs = cursor.fetchall()
    cursor.close()
    db.close()

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow(['Audit ID', 'Performed By', 'Role', 'Action',
                     'Table Affected', 'Record ID', 'Details', 'Performed At'])

    # Data rows
    for log in logs:
        writer.writerow([
            log['audit_id'],
            log['performed_by'],
            log['role'],
            log['action'],
            log.get('table_affected') or '',
            log.get('record_id') or '',
            log.get('details') or '',
            log['performed_at'].strftime('%Y-%m-%d %H:%M:%S')
            if log['performed_at'].__class__.__name__ == 'datetime'
            else log['performed_at']
        ])

    output.seek(0)

    # Build filename with date
    from datetime import date
    filename = f"audit_trail_{date.today().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={filename}'
        }
    )


    # ── Login Logs ──────────────────

@ecp_bp.route('/ecp/login-logs')
@ecp_required
def login_logs():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Read filter params
    status_filter   = request.args.get('status', '').strip()
    role_filter     = request.args.get('role', '').strip()
    username_filter = request.args.get('username', '').strip()
    date_from       = request.args.get('date_from', '').strip()
    date_to         = request.args.get('date_to', '').strip()

    # Build query dynamically
    query  = "SELECT * FROM login_logs WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    if role_filter:
        query += " AND role = %s"
        params.append(role_filter)

    if username_filter:
        query += " AND username LIKE %s"
        params.append(f'%{username_filter}%')

    if date_from:
        query += " AND DATE(login_time) >= %s"
        params.append(date_from)

    if date_to:
        query += " AND DATE(login_time) <= %s"
        params.append(date_to)

    query += " ORDER BY login_time DESC LIMIT 200"

    cursor.execute(query, params)
    logs = cursor.fetchall()

    # Total count without limit
    count_query  = "SELECT COUNT(*) AS total FROM login_logs WHERE 1=1"
    count_params = []

    if status_filter:
        count_query += " AND status = %s"
        count_params.append(status_filter)

    if role_filter:
        count_query += " AND role = %s"
        count_params.append(role_filter)

    if username_filter:
        count_query += " AND username LIKE %s"
        count_params.append(f'%{username_filter}%')

    if date_from:
        count_query += " AND DATE(login_time) >= %s"
        count_params.append(date_from)

    if date_to:
        count_query += " AND DATE(login_time) <= %s"
        count_params.append(date_to)

    cursor.execute(count_query, count_params)
    total_count = cursor.fetchone()['total']

    # Summary stats for top cards
    cursor.execute("SELECT COUNT(*) AS c FROM login_logs WHERE status = 'Success'")
    total_success = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM login_logs WHERE status = 'Failed'")
    total_failed = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM login_logs")
    total_all = cursor.fetchone()['c']

    # Suspicious IPs — more than 5 failed attempts in last hour
    cursor.execute("""
        SELECT ip_address, COUNT(*) AS attempts
        FROM login_logs
        WHERE status = 'Failed'
        AND login_time >= NOW() - INTERVAL 1 HOUR
        GROUP BY ip_address
        HAVING attempts >= 5
        ORDER BY attempts DESC
    """)
    suspicious_ips = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template('ecp/login_logs.html',
        username=session.get('username'),
        logs=logs,
        total_count=total_count,
        total_success=total_success,
        total_failed=total_failed,
        total_all=total_all,
        suspicious_ips=suspicious_ips,
        status_filter=status_filter,
        role_filter=role_filter,
        username_filter=username_filter,
        date_from=date_from,
        date_to=date_to
    )


@ecp_bp.route('/ecp/login-logs/export')
@ecp_required
def export_login_csv():
    import csv
    import io

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Same filters
    status_filter   = request.args.get('status', '').strip()
    role_filter     = request.args.get('role', '').strip()
    username_filter = request.args.get('username', '').strip()
    date_from       = request.args.get('date_from', '').strip()
    date_to         = request.args.get('date_to', '').strip()

    query  = "SELECT * FROM login_logs WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    if role_filter:
        query += " AND role = %s"
        params.append(role_filter)

    if username_filter:
        query += " AND username LIKE %s"
        params.append(f'%{username_filter}%')

    if date_from:
        query += " AND DATE(login_time) >= %s"
        params.append(date_from)

    if date_to:
        query += " AND DATE(login_time) <= %s"
        params.append(date_to)

    query += " ORDER BY login_time DESC"

    cursor.execute(query, params)
    logs = cursor.fetchall()
    cursor.close()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['Log ID', 'Username', 'Role', 'IP Address',
                     'Login Time', 'Status'])

    for log in logs:
        writer.writerow([
            log['log_id'],
            log['username'],
            log['role'],
            log.get('ip_address') or '—',
            log['login_time'].strftime('%Y-%m-%d %H:%M:%S')
            if log['login_time'].__class__.__name__ == 'datetime'
            else log['login_time'],
            log['status']
        ])

    output.seek(0)

    from datetime import date
    filename = f"login_logs_{date.today().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ── Results ────────────────────────────────────────────────────────────────


@ecp_bp.route('/ecp/results')
@ecp_required
def results():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # ── Active election ────────────────────────────────────────────
    cursor.execute("""
        SELECT * FROM elections
        ORDER BY created_at DESC LIMIT 1
    """)
    election = cursor.fetchone()

    if not election:
        cursor.close()
        db.close()
        return render_template('ecp/results.html',
            username=session.get('username'),
            election=None,
            summary={},
            party_tally=[],
            na_results=[],
            pa_results=[],
            province_filter='',
            type_filter='',
            provinces=[]
        )

    election_id = election['election_id']

    # ── Read filters ───────────────────────────────────────────────
    province_filter = request.args.get('province', '').strip()
    type_filter     = request.args.get('type', '').strip()

    # ── Summary stats ──────────────────────────────────────────────
    cursor.execute("""
        SELECT COUNT(*) AS c FROM votes_na
        WHERE election_id = %s
    """, (election_id,))
    total_na_votes = cursor.fetchone()['c']

    cursor.execute("""
        SELECT COUNT(*) AS c FROM votes_pa
        WHERE election_id = %s
    """, (election_id,))
    total_pa_votes = cursor.fetchone()['c']

    total_votes_cast = total_na_votes + total_pa_votes

    cursor.execute("SELECT COUNT(*) AS c FROM voters")
    total_registered = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM voters WHERE has_voted = 1")
    total_voted = cursor.fetchone()['c']

    turnout_pct = round((total_voted / total_registered * 100), 1) if total_registered > 0 else 0

    cursor.execute("""
        SELECT COUNT(DISTINCT na_id) AS c FROM votes_na
        WHERE election_id = %s
    """, (election_id,))
    na_seats_active = cursor.fetchone()['c']

    cursor.execute("""
        SELECT COUNT(DISTINCT pa_id) AS c FROM votes_pa
        WHERE election_id = %s
    """, (election_id,))
    pa_seats_active = cursor.fetchone()['c']

    summary = {
        'total_votes_cast': total_votes_cast,
        'total_na_votes':   total_na_votes,
        'total_pa_votes':   total_pa_votes,
        'total_registered': total_registered,
        'total_voted':      total_voted,
        'turnout_pct':      turnout_pct,
        'na_seats_active':  na_seats_active,
        'pa_seats_active':  pa_seats_active,
    }

    # ── Party seat tally ───────────────────────────────────────────
    # NA seats per party
    cursor.execute("""
        SELECT p.party_name, p.abbreviation,
               COUNT(DISTINCT vn.na_id) AS na_seats_leading
        FROM votes_na vn
        JOIN candidates c ON vn.candidate_id = c.candidate_id
        JOIN parties p ON c.party_id = p.party_id
        WHERE vn.election_id = %s
          AND vn.candidate_id = (
              SELECT candidate_id FROM votes_na v2
              WHERE v2.na_id = vn.na_id
                AND v2.election_id = vn.election_id
              GROUP BY candidate_id
              ORDER BY COUNT(*) DESC
              LIMIT 1
          )
        GROUP BY p.party_id
        ORDER BY na_seats_leading DESC
    """, (election_id,))
    na_tally = {row['abbreviation']: row['na_seats_leading']
                for row in cursor.fetchall()}

    # PA seats per party
    cursor.execute("""
        SELECT p.party_name, p.abbreviation,
               COUNT(DISTINCT vp.pa_id) AS pa_seats_leading
        FROM votes_pa vp
        JOIN candidates c ON vp.candidate_id = c.candidate_id
        JOIN parties p ON c.party_id = p.party_id
        WHERE vp.election_id = %s
          AND vp.candidate_id = (
              SELECT candidate_id FROM votes_pa v2
              WHERE v2.pa_id = vp.pa_id
                AND v2.election_id = vp.election_id
              GROUP BY candidate_id
              ORDER BY COUNT(*) DESC
              LIMIT 1
          )
        GROUP BY p.party_id
        ORDER BY pa_seats_leading DESC
    """, (election_id,))
    pa_tally = {row['abbreviation']: row['pa_seats_leading']
                for row in cursor.fetchall()}

    # Merge into one tally list
    all_abbrs = set(list(na_tally.keys()) + list(pa_tally.keys()))
    party_tally = []
    for abbr in all_abbrs:
        cursor.execute("""
            SELECT party_name, abbreviation FROM parties
            WHERE abbreviation = %s
        """, (abbr,))
        p = cursor.fetchone()
        if p:
            party_tally.append({
                'party_name': p['party_name'],
                'abbreviation': abbr,
                'na_seats': na_tally.get(abbr, 0),
                'pa_seats': pa_tally.get(abbr, 0),
                'total_seats': na_tally.get(abbr, 0) + pa_tally.get(abbr, 0),
            })

    party_tally.sort(key=lambda x: x['total_seats'], reverse=True)

    # ── NA Results ─────────────────────────────────────────────────
    na_filter_clause = ""
    na_filter_params = [election_id]

    if province_filter:
        na_filter_clause += " AND cn.province_id = %s"
        na_filter_params.append(province_filter)

    cursor.execute(f"""
        SELECT cn.na_id, cn.na_number, cn.na_name,
               c.candidate_id, c.full_name,
               p.party_name, p.abbreviation,
               COUNT(vn.vote_id) AS vote_count,
               (SELECT COUNT(*) FROM votes_na v2
                WHERE v2.na_id = cn.na_id
                  AND v2.election_id = %s) AS total_con_votes,
               (SELECT COUNT(*) FROM voters v3
                WHERE v3.constituency_na_id = cn.na_id) AS registered_in_con
        FROM constituencies_na cn
        JOIN candidate_na cna ON cn.na_id = cna.na_id
        JOIN candidates c ON cna.candidate_id = c.candidate_id
        JOIN parties p ON c.party_id = p.party_id
        LEFT JOIN votes_na vn ON vn.candidate_id = c.candidate_id
            AND vn.na_id = cn.na_id
            AND vn.election_id = cna.election_id
        WHERE cna.election_id = %s
        {na_filter_clause}
        GROUP BY cn.na_id, c.candidate_id
        ORDER BY cn.na_number, vote_count DESC
    """, [election_id] + na_filter_params)
    na_raw = cursor.fetchall()

    # Group NA results by constituency
    na_results = {}
    for row in na_raw:
        nid = row['na_id']
        if nid not in na_results:
            na_results[nid] = {
                'na_number':        row['na_number'],
                'na_name':          row['na_name'],
                'total_con_votes':  row['total_con_votes'],
                'registered':       row['registered_in_con'],
                'turnout':          round(row['total_con_votes'] / row['registered_in_con'] * 100, 1)
                                    if row['registered_in_con'] > 0 else 0,
                'candidates':       []
            }
        na_results[nid]['candidates'].append({
            'name':        row['full_name'],
            'party':       row['abbreviation'],
            'party_full':  row['party_name'],
            'votes':       row['vote_count'],
            'pct':         round(row['vote_count'] / row['total_con_votes'] * 100, 1)
                           if row['total_con_votes'] > 0 else 0
        })

    # ── PA Results ─────────────────────────────────────────────────
    pa_filter_clause = ""
    pa_filter_params = [election_id]

    if province_filter:
        pa_filter_clause += " AND cp.province_id = %s"
        pa_filter_params.append(province_filter)

    cursor.execute(f"""
        SELECT cp.pa_id, cp.pa_number, cp.pa_name,
               c.candidate_id, c.full_name,
               p.party_name, p.abbreviation,
               COUNT(vp.vote_id) AS vote_count,
               (SELECT COUNT(*) FROM votes_pa v2
                WHERE v2.pa_id = cp.pa_id
                  AND v2.election_id = %s) AS total_con_votes,
               (SELECT COUNT(*) FROM voters v3
                WHERE v3.constituency_pa_id = cp.pa_id) AS registered_in_con
        FROM constituencies_pa cp
        JOIN candidate_pa cpa ON cp.pa_id = cpa.pa_id
        JOIN candidates c ON cpa.candidate_id = c.candidate_id
        JOIN parties p ON c.party_id = p.party_id
        LEFT JOIN votes_pa vp ON vp.candidate_id = c.candidate_id
            AND vp.pa_id = cp.pa_id
            AND vp.election_id = cpa.election_id
        WHERE cpa.election_id = %s
        {pa_filter_clause}
        GROUP BY cp.pa_id, c.candidate_id
        ORDER BY cp.pa_number, vote_count DESC
    """, [election_id] + pa_filter_params)
    pa_raw = cursor.fetchall()

    # Group PA results by constituency
    pa_results = {}
    for row in pa_raw:
        pid = row['pa_id']
        if pid not in pa_results:
            pa_results[pid] = {
                'pa_number':       row['pa_number'],
                'pa_name':         row['pa_name'],
                'total_con_votes': row['total_con_votes'],
                'registered':      row['registered_in_con'],
                'turnout':         round(row['total_con_votes'] / row['registered_in_con'] * 100, 1)
                                   if row['registered_in_con'] > 0 else 0,
                'candidates':      []
            }
        pa_results[pid]['candidates'].append({
            'name':       row['full_name'],
            'party':      row['abbreviation'],
            'party_full': row['party_name'],
            'votes':      row['vote_count'],
            'pct':        round(row['vote_count'] / row['total_con_votes'] * 100, 1)
                          if row['total_con_votes'] > 0 else 0
        })

    # ── Provinces for filter dropdown ──────────────────────────────
    cursor.execute("SELECT province_id, province_name FROM provinces ORDER BY province_name")
    provinces = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template('ecp/results.html',
        username=session.get('username'),
        election=election,
        summary=summary,
        party_tally=party_tally,
        na_results=list(na_results.values()),
        pa_results=list(pa_results.values()),
        province_filter=province_filter,
        type_filter=type_filter,
        provinces=provinces
    )
    
# ══════════════════════════════════════════════════════════════════════════════
# ISLAMABAD — ECP DIRECT CONTROL
# Islamabad province_id = 5, is_federal_territory = 1
# ══════════════════════════════════════════════════════════════════════════════

ISLAMABAD_PROVINCE_ID = 5

# ── Islamabad Main Management Page ────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad')
@ecp_required
def islamabad():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Stats
    cursor.execute("SELECT COUNT(*) AS c FROM voters WHERE province_id = %s",
                   (ISLAMABAD_PROVINCE_ID,))
    total_voters = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM candidates WHERE province_id = %s",
                   (ISLAMABAD_PROVINCE_ID,))
    total_candidates = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM polling_stations WHERE province_id = %s",
                   (ISLAMABAD_PROVINCE_ID,))
    total_stations = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM polling_officers WHERE province_id = %s",
                   (ISLAMABAD_PROVINCE_ID,))
    total_po = cursor.fetchone()['c']

    # Polling stations with officer names
    cursor.execute("""
        SELECT ps.station_id, ps.station_name, ps.address, ps.is_active,
               c.city_name, po.full_name AS officer_name
        FROM polling_stations ps
        JOIN cities c ON ps.city_id = c.city_id
        LEFT JOIN polling_officers po
            ON ps.station_id = po.station_id AND po.is_active = 1
        WHERE ps.province_id = %s
        ORDER BY ps.created_at DESC
    """, (ISLAMABAD_PROVINCE_ID,))
    stations = cursor.fetchall()

    # Polling officers
    cursor.execute("""
        SELECT po.po_id, po.full_name, po.username, po.is_active, po.last_login,
               ps.station_name
        FROM polling_officers po
        JOIN polling_stations ps ON po.station_id = ps.station_id
        WHERE po.province_id = %s
        ORDER BY po.created_at DESC
    """, (ISLAMABAD_PROVINCE_ID,))
    officers = cursor.fetchall()

    # Stations with no active officer (for add officer form)
    cursor.execute("""
        SELECT ps.station_id, ps.station_name, c.city_name
        FROM polling_stations ps
        JOIN cities c ON ps.city_id = c.city_id
        WHERE ps.province_id = %s
          AND ps.station_id NOT IN (
              SELECT station_id FROM polling_officers
              WHERE is_active = 1 AND province_id = %s
          )
        ORDER BY ps.station_name
    """, (ISLAMABAD_PROVINCE_ID, ISLAMABAD_PROVINCE_ID))
    available_stations = cursor.fetchall()

    # Islamabad cities
    cursor.execute("""
        SELECT city_id, city_name FROM cities
        WHERE province_id = %s ORDER BY city_name
    """, (ISLAMABAD_PROVINCE_ID,))
    cities = cursor.fetchall()

    # Islamabad NA constituencies (no PA seats)
    cursor.execute("""
        SELECT na_id, na_number, na_name FROM constituencies_na
        WHERE province_id = %s ORDER BY na_number
    """, (ISLAMABAD_PROVINCE_ID,))
    na_list = cursor.fetchall()

    # Parties for candidate form
    cursor.execute("""
        SELECT party_id, party_name, abbreviation FROM parties
        WHERE is_active = 1 ORDER BY party_name
    """)
    parties = cursor.fetchall()

    # Active election
    cursor.execute("""
        SELECT election_id FROM elections
        WHERE status IN ('Active','Upcoming')
        ORDER BY created_at DESC LIMIT 1
    """)
    election = cursor.fetchone()

    # Candidates
    cursor.execute("""
        SELECT c.candidate_id, c.full_name, c.cnic, c.is_active,
               p.party_name, p.abbreviation,
               (SELECT na.na_number FROM constituencies_na na
                JOIN candidate_na cna ON na.na_id = cna.na_id
                WHERE cna.candidate_id = c.candidate_id LIMIT 1) AS na_number
        FROM candidates c
        JOIN parties p ON c.party_id = p.party_id
        WHERE c.province_id = %s
        ORDER BY c.created_at DESC
    """, (ISLAMABAD_PROVINCE_ID,))
    candidates = cursor.fetchall()

    # Voters
    cursor.execute("""
        SELECT v.voter_id, v.cnic, v.full_name, v.gender, v.has_voted,
               c.city_name, cn.na_number
        FROM voters v
        JOIN cities c ON v.city_id = c.city_id
        JOIN constituencies_na cn ON v.constituency_na_id = cn.na_id
        WHERE v.province_id = %s
        ORDER BY v.full_name ASC LIMIT 200
    """, (ISLAMABAD_PROVINCE_ID,))
    voters = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*) AS c FROM voters WHERE province_id = %s
    """, (ISLAMABAD_PROVINCE_ID,))
    voter_count = cursor.fetchone()['c']

    cursor.close(); db.close()

    return render_template('ecp/islamabad.html',
        username=session.get('username'),
        total_voters=total_voters,
        total_candidates=total_candidates,
        total_stations=total_stations,
        total_po=total_po,
        stations=stations,
        officers=officers,
        available_stations=available_stations,
        cities=cities,
        na_list=na_list,
        parties=parties,
        election_id=election['election_id'] if election else None,
        candidates=candidates,
        voters=voters,
        voter_count=voter_count
    )


# ── Add Polling Station ────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/stations/add', methods=['POST'])
@ecp_required
def islamabad_add_station():
    station_name = request.form['station_name'].strip()
    address      = request.form['address'].strip()
    city_id      = request.form['city_id']

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT province_id FROM cities WHERE city_id = %s
    """, (city_id,))
    city = cursor.fetchone()
    if not city or city['province_id'] != ISLAMABAD_PROVINCE_ID:
        flash('Invalid city.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("""
        SELECT station_id FROM polling_stations
        WHERE station_name = %s AND city_id = %s
    """, (station_name, city_id))
    if cursor.fetchone():
        flash('Station with this name already exists.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("""
        INSERT INTO polling_stations
            (station_name, address, city_id, province_id)
        VALUES (%s, %s, %s, %s)
    """, (station_name, address, city_id, ISLAMABAD_PROVINCE_ID))
    db.commit()
    sid = cursor.lastrowid

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected, record_id)
        VALUES (%s, 'ECP', %s, 'polling_stations', %s)
    """, (session.get('username'),
          f'ICT: Added polling station: {station_name}', sid))
    db.commit()
    cursor.close(); db.close()

    flash(f'Station "{station_name}" added.', 'success')
    return redirect(url_for('ecp.islamabad') + '#stations')


# ── Delete Polling Station ─────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/stations/delete/<int:station_id>', methods=['POST'])
@ecp_required
def islamabad_delete_station(station_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT station_name, province_id FROM polling_stations
        WHERE station_id = %s
    """, (station_id,))
    station = cursor.fetchone()

    if not station or station['province_id'] != ISLAMABAD_PROVINCE_ID:
        flash('Station not found.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("""
        SELECT po_id FROM polling_officers
        WHERE station_id = %s AND is_active = 1
    """, (station_id,))
    if cursor.fetchone():
        flash('Cannot delete — station has an active officer.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("DELETE FROM polling_stations WHERE station_id = %s", (station_id,))
    db.commit()
    cursor.close(); db.close()

    flash('Station deleted.', 'success')
    return redirect(url_for('ecp.islamabad') + '#stations')


# ── Add Polling Officer ────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/officers/add', methods=['POST'])
@ecp_required
def islamabad_add_officer():
    full_name  = request.form['full_name'].strip()
    username   = request.form['username'].strip()
    password   = request.form['password']
    confirm    = request.form['confirm_password']
    station_id = request.form['station_id']

    if password != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('ecp.islamabad'))

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('ecp.islamabad'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT po_id FROM polling_officers WHERE username = %s", (username,))
    if cursor.fetchone():
        flash('Username already taken.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("""
        SELECT po_id FROM polling_officers
        WHERE station_id = %s AND is_active = 1
    """, (station_id,))
    if cursor.fetchone():
        flash('This station already has an active officer.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("""
        INSERT INTO polling_officers
            (full_name, username, password_hash, station_id, province_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (full_name, username, hash_password(password),
        station_id, ISLAMABAD_PROVINCE_ID))
    db.commit()
    po_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected, record_id)
        VALUES (%s, 'ECP', %s, 'polling_officers', %s)
    """, (session.get('username'),
          f'ICT: Added Polling Officer: {full_name} ({username})', po_id))
    db.commit()
    cursor.close(); db.close()

    flash(f'Polling Officer {full_name} added.', 'success')
    return redirect(url_for('ecp.islamabad') + '#officers')


# ── Toggle Polling Officer ─────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/officers/toggle/<int:po_id>', methods=['POST'])
@ecp_required
def islamabad_toggle_officer(po_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username, is_active, province_id
        FROM polling_officers WHERE po_id = %s
    """, (po_id,))
    officer = cursor.fetchone()

    if not officer or officer['province_id'] != ISLAMABAD_PROVINCE_ID:
        flash('Officer not found.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    new_status = 0 if officer['is_active'] else 1
    word = 'Deactivated' if new_status == 0 else 'Activated'

    cursor.execute("UPDATE polling_officers SET is_active = %s WHERE po_id = %s",
                   (new_status, po_id))
    db.commit()
    cursor.close(); db.close()

    flash(f'Officer {word.lower()}.', 'success')
    return redirect(url_for('ecp.islamabad') + '#officers')


# ── Add Candidate ──────────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/candidates/add', methods=['POST'])
@ecp_required
def islamabad_add_candidate():
    full_name   = request.form['full_name'].strip()
    cnic        = request.form['cnic'].strip()
    party_id    = request.form['party_id']
    na_id       = request.form['na_id']
    election_id = request.form.get('election_id')
    photo_path  = None

    if 'photo' in request.files:
        file = request.files['photo']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            save_dir = os.path.join('static', 'images', 'uploads', 'candidates')
            os.makedirs(save_dir, exist_ok=True)
            file.save(os.path.join(save_dir, filename))
            photo_path = filename

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT candidate_id FROM candidates WHERE cnic = %s", (cnic,))
    if cursor.fetchone():
        flash('A candidate with this CNIC already exists.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("""
        INSERT INTO candidates
            (full_name, cnic, photo_path, party_id,
             province_id, added_by_officer)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (full_name, cnic, photo_path, party_id,
          ISLAMABAD_PROVINCE_ID, None))
    db.commit()
    cand_id = cursor.lastrowid

    if election_id and na_id:
        cursor.execute("""
            INSERT IGNORE INTO candidate_na (candidate_id, na_id, election_id)
            VALUES (%s, %s, %s)
        """, (cand_id, na_id, election_id))
        db.commit()

    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected, record_id)
        VALUES (%s, 'ECP', %s, 'candidates', %s)
    """, (session.get('username'),
          f'ICT: Added candidate: {full_name} ({cnic})', cand_id))
    db.commit()
    cursor.close(); db.close()

    flash(f'Candidate {full_name} added.', 'success')
    return redirect(url_for('ecp.islamabad') + '#candidates')


# ── Delete Candidate ───────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/candidates/delete/<int:cand_id>', methods=['POST'])
@ecp_required
def islamabad_delete_candidate(cand_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, province_id FROM candidates WHERE candidate_id = %s
    """, (cand_id,))
    cand = cursor.fetchone()

    if not cand or cand['province_id'] != ISLAMABAD_PROVINCE_ID:
        flash('Candidate not found.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("SELECT COUNT(*) AS c FROM votes_na WHERE candidate_id = %s", (cand_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — candidate has votes recorded.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad'))

    cursor.execute("DELETE FROM candidate_na WHERE candidate_id = %s", (cand_id,))
    cursor.execute("DELETE FROM candidates WHERE candidate_id = %s", (cand_id,))
    db.commit()
    cursor.close(); db.close()

    flash('Candidate deleted.', 'success')
    return redirect(url_for('ecp.islamabad') + '#candidates')


# ── Import Voters CSV ──────────────────────────────────────────────────────────

@ecp_bp.route('/ecp/islamabad/voters/import', methods=['POST'])
@ecp_required
def islamabad_import_voters():
    

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT city_id FROM cities WHERE province_id = %s",
                   (ISLAMABAD_PROVINCE_ID,))
    valid_cities = {str(r['city_id']) for r in cursor.fetchall()}

    cursor.execute("SELECT na_id FROM constituencies_na WHERE province_id = %s",
                   (ISLAMABAD_PROVINCE_ID,))
    valid_na = {str(r['na_id']) for r in cursor.fetchall()}

    if 'csv_file' not in request.files or request.files['csv_file'].filename == '':
        flash('No file uploaded.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad') + '#voters')

    file    = request.files['csv_file']
    content = file.read().decode('utf-8-sig')
    reader  = csv.DictReader(io.StringIO(content))

    required_cols = {'cnic', 'full_name', 'date_of_birth',
                     'gender', 'city_id', 'constituency_na_id'}
    if not required_cols.issubset(set(reader.fieldnames or [])):
        missing = required_cols - set(reader.fieldnames or [])
        flash(f'Missing CSV columns: {", ".join(missing)}', 'error')
        cursor.close(); db.close()
        return redirect(url_for('ecp.islamabad') + '#voters')






    inserted = 0
    skipped  = 0

    for i, row in enumerate(reader, start=1):
        cnic      = (row.get('cnic') or '').strip()
        full_name = (row.get('full_name') or '').strip()
        dob       = (row.get('date_of_birth') or '').strip()
        gender    = (row.get('gender') or '').strip()
        city_id   = (row.get('city_id') or '').strip()
        na_id     = (row.get('constituency_na_id') or '').strip()

        reason = None

        if not all([cnic, full_name, dob, gender, city_id, na_id]):
            reason = "Missing fields"

        elif not validate_cnic_format(cnic):
            reason = "Invalid CNIC format"

        else:
            cnic_info = get_province_from_cnic(cnic)

            if cnic_info is None:
                reason = "CNIC province not detected"

            elif cnic_info[2] != ISLAMABAD_PROVINCE_ID:
                reason = f"Wrong province (CNIC says {cnic_info[2]})"

            elif city_id not in valid_cities:
                reason = f"Invalid city_id ({city_id})"

            elif na_id not in valid_na:
                reason = f"Invalid NA ID ({na_id})"

        if reason:
            print(f"Row {i} skipped: {reason} → {row}")
            skipped += 1
            continue

        try:
            cursor.execute("""
                INSERT INTO voters
                (cnic, full_name, date_of_birth, gender,
                province_id, city_id, constituency_na_id,
                constituency_pa_id, registered_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
                ON DUPLICATE KEY UPDATE
                    full_name          = VALUES(full_name),
                    date_of_birth      = VALUES(date_of_birth),
                    gender             = VALUES(gender),
                    city_id            = VALUES(city_id),
                    constituency_na_id = VALUES(constituency_na_id)
            """, (
                cnic, full_name, dob, gender,
                ISLAMABAD_PROVINCE_ID, city_id, na_id
            ))
            inserted += 1

        except Exception as e:
            print(f"Row {i} DB ERROR: {e}")
            skipped += 1





    db.commit()
    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected)
        VALUES (%s, 'ECP', %s, 'voters')
    """, (session.get('username'),
          f'ICT: Imported {inserted} voters via CSV ({skipped} skipped)'))
    db.commit()
    cursor.close(); db.close()

    flash(f'Import complete — {inserted} voters added, {skipped} skipped.', 'success')
    return redirect(url_for('ecp.islamabad') + '#voters')


