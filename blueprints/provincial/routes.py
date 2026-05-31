from flask import Blueprint, render_template, session, redirect, url_for, request, flash, Response
from database.db import get_db
from functools import wraps
import hashlib, secrets, string, os
from werkzeug.utils import secure_filename
from blueprints.voter.cnic_utils import get_province_from_cnic, validate_cnic_format
import csv, io
   


provincial_bp = Blueprint('provincial', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def provincial_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'Provincial Officer':
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

def log_action(cursor, username, action, table=None, record_id=None):
    cursor.execute("""
        INSERT INTO audit_trail (performed_by, role, action, table_affected, record_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (username, 'Provincial Officer', action, table, record_id))


# ── Dashboard ──────────────────────────────────────────────────────────────

@provincial_bp.route('/provincial/dashboard')
@provincial_required
def dashboard():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    cursor.execute("SELECT province_name FROM provinces WHERE province_id = %s", (pid,))
    province = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) AS c FROM voters WHERE province_id = %s", (pid,))
    total_voters = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM candidates WHERE province_id = %s", (pid,))
    total_candidates = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM polling_stations WHERE province_id = %s", (pid,))
    total_stations = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) AS c FROM polling_officers WHERE province_id = %s", (pid,))
    total_po = cursor.fetchone()['c']

    cursor.execute("""
        SELECT * FROM audit_trail
        WHERE performed_by = %s
        ORDER BY performed_at DESC LIMIT 10
    """, (session['username'],))
    audit_logs = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template('provincial/dashboard.html',
        username=session['username'],
        province_name=province['province_name'],
        total_voters=total_voters,
        total_candidates=total_candidates,
        total_stations=total_stations,
        total_po=total_po,
        audit_logs=audit_logs
    )


# ── Candidates ─────────────────────────────────────────────────────────────

@provincial_bp.route('/provincial/candidates')
@provincial_required
def candidates():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    type_filter = request.args.get('type', '').strip()

    # Subquery approach — no duplicates if candidate appears in both tables
    query = """
        SELECT c.candidate_id, c.full_name, c.cnic, c.photo_path, c.is_active,
               p.party_name, p.abbreviation, p.party_color,
               (SELECT na.na_number FROM constituencies_na na
                JOIN candidate_na cna ON na.na_id = cna.na_id
                WHERE cna.candidate_id = c.candidate_id LIMIT 1) AS na_number,
               (SELECT pa.pa_number FROM constituencies_pa pa
                JOIN candidate_pa cpa ON pa.pa_id = cpa.pa_id
                WHERE cpa.candidate_id = c.candidate_id LIMIT 1) AS pa_number
        FROM candidates c
        JOIN parties p ON c.party_id = p.party_id
        WHERE c.province_id = %s
    """
    params = [pid]

    if type_filter == 'NA':
        query += " AND EXISTS (SELECT 1 FROM candidate_na WHERE candidate_id = c.candidate_id)"
    elif type_filter == 'PA':
        query += " AND EXISTS (SELECT 1 FROM candidate_pa WHERE candidate_id = c.candidate_id)"

    query += " ORDER BY c.created_at DESC"
    cursor.execute(query, params)
    all_candidates = cursor.fetchall()

    cursor.execute("""
        SELECT party_id, party_name, abbreviation, party_color
        FROM parties WHERE is_active = 1 ORDER BY party_name
    """)
    parties = cursor.fetchall()

    cursor.execute("""
        SELECT na_id, na_number, na_name FROM constituencies_na
        WHERE province_id = %s ORDER BY na_number
    """, (pid,))
    na_cons = cursor.fetchall()

    cursor.execute("""
        SELECT pa_id, pa_number, pa_name FROM constituencies_pa
        WHERE province_id = %s ORDER BY pa_number
    """, (pid,))
    pa_cons = cursor.fetchall()

    cursor.execute("""
    SELECT election_id, status FROM elections
    WHERE status = 'Upcoming'
    ORDER BY created_at DESC LIMIT 1
    """)
    election = cursor.fetchone()
    election_id = election['election_id'] if election else None

    cursor.close()
    db.close()

    return render_template('provincial/candidates.html',
        username=session['username'],
        candidates=all_candidates,
        parties=parties,
        na_cons=na_cons,
        pa_cons=pa_cons,
        election_id=election_id,
        type_filter=type_filter
    )


@provincial_bp.route('/provincial/candidates/add', methods=['POST'])
@provincial_required
def add_candidate():
    full_name   = request.form['full_name'].strip()
    cnic        = request.form['cnic'].strip()
    party_id    = request.form['party_id']
    con_type    = request.form['con_type']
    con_id      = request.form['con_id']
    election_id = request.form.get('election_id') or None
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
        return redirect(url_for('provincial.candidates'))

    cursor.execute("""
        INSERT INTO candidates (full_name, cnic, photo_path, party_id, province_id, added_by_officer)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (full_name, cnic, photo_path, party_id, session['province_id'], session['user_id']))
    db.commit()
    cand_id = cursor.lastrowid

    if election_id and con_id:
        if con_type == 'NA':
            cursor.execute("""
                INSERT IGNORE INTO candidate_na (candidate_id, na_id, election_id)
                VALUES (%s, %s, %s)
            """, (cand_id, con_id, election_id))
        elif con_type == 'PA':
            cursor.execute("""
                INSERT IGNORE INTO candidate_pa (candidate_id, pa_id, election_id)
                VALUES (%s, %s, %s)
            """, (cand_id, con_id, election_id))
        db.commit()

    log_action(cursor, session['username'],
               f'Added candidate: {full_name} ({cnic}) — {con_type}', 'candidates', cand_id)
    db.commit()

    cursor.close(); db.close()
    flash(f'Candidate {full_name} added successfully.', 'success')
    return redirect(url_for('provincial.candidates'))


@provincial_bp.route('/provincial/candidates/delete/<int:cand_id>', methods=['POST'])
@provincial_required
def delete_candidate(cand_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, province_id FROM candidates WHERE candidate_id = %s
    """, (cand_id,))
    cand = cursor.fetchone()

    if not cand or cand['province_id'] != session['province_id']:
        flash('Candidate not found or access denied.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.candidates'))

    cursor.execute("SELECT COUNT(*) AS c FROM votes_na WHERE candidate_id = %s", (cand_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — this candidate has votes recorded.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.candidates'))

    cursor.execute("SELECT COUNT(*) AS c FROM votes_pa WHERE candidate_id = %s", (cand_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — this candidate has votes recorded.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.candidates'))

    cursor.execute("DELETE FROM candidate_na WHERE candidate_id = %s", (cand_id,))
    cursor.execute("DELETE FROM candidate_pa WHERE candidate_id = %s", (cand_id,))
    cursor.execute("DELETE FROM candidates WHERE candidate_id = %s", (cand_id,))
    db.commit()

    log_action(cursor, session['username'],
               f"Deleted candidate: {cand['full_name']}", 'candidates', cand_id)
    db.commit()

    cursor.close(); db.close()
    flash('Candidate deleted.', 'success')
    return redirect(url_for('provincial.candidates'))



# ── Voters (READ-ONLY + CSV Export) ───────────────────────────────────────

@provincial_bp.route('/provincial/voters')
@provincial_required
def voters():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    search_cnic   = request.args.get('cnic', '').strip()
    gender_filter = request.args.get('gender', '').strip()
    city_filter   = request.args.get('city_id', '').strip()
    voted_filter  = request.args.get('voted', '').strip()

    query = """
        SELECT v.voter_id, v.cnic, v.full_name, v.gender, v.has_voted,
               c.city_name,
               cn.na_number, cp.pa_number
        FROM voters v
        JOIN cities c ON v.city_id = c.city_id
        JOIN constituencies_na cn ON v.constituency_na_id = cn.na_id
        LEFT JOIN constituencies_pa cp ON v.constituency_pa_id = cp.pa_id
        WHERE v.province_id = %s
    """
    params = [pid]

    if search_cnic:
        query += " AND v.cnic LIKE %s"
        params.append(f'%{search_cnic}%')
    if gender_filter:
        query += " AND v.gender = %s"
        params.append(gender_filter)
    if city_filter:
        query += " AND v.city_id = %s"
        params.append(city_filter)
    if voted_filter == '1':
        query += " AND v.has_voted = 1"
    elif voted_filter == '0':
        query += " AND v.has_voted = 0"

    query += " ORDER BY v.full_name ASC LIMIT 200"
    cursor.execute(query, params)
    all_voters = cursor.fetchall()

    count_q = "SELECT COUNT(*) AS c FROM voters v WHERE v.province_id = %s"
    count_p = [pid]
    if search_cnic:
        count_q += " AND v.cnic LIKE %s"; count_p.append(f'%{search_cnic}%')
    if gender_filter:
        count_q += " AND v.gender = %s"; count_p.append(gender_filter)
    if city_filter:
        count_q += " AND v.city_id = %s"; count_p.append(city_filter)
    if voted_filter == '1':
        count_q += " AND v.has_voted = 1"
    elif voted_filter == '0':
        count_q += " AND v.has_voted = 0"
    cursor.execute(count_q, count_p)
    total_count = cursor.fetchone()['c']

    cursor.execute("""
        SELECT city_id, city_name FROM cities
        WHERE province_id = %s ORDER BY city_name
    """, (pid,))
    cities = cursor.fetchall()

    cursor.close(); db.close()

    return render_template('provincial/voters.html',
        username=session['username'],
        voters=all_voters,
        total_count=total_count,
        cities=cities,
        search_cnic=search_cnic,
        gender_filter=gender_filter,
        city_filter=city_filter,
        voted_filter=voted_filter
    )


# ── CSV Import ─────────────────────────────────────────────────────────────

@provincial_bp.route('/provincial/voters/import', methods=['GET', 'POST'])
@provincial_required
def import_voters():
    """
    Provincial Officer uploads a CSV of voters.

    Expected CSV columns (header row required):
        cnic, full_name, date_of_birth, gender, city_id,
        constituency_na_id, constituency_pa_id

    date_of_birth format: YYYY-MM-DD
    gender: Male / Female / Other
    city_id, constituency_na_id, constituency_pa_id: numeric IDs from your DB
    """


    pid = session['province_id']
    db  = get_db()
    cursor = db.cursor(dictionary=True)

    # Load cities and constituencies for the reference table shown on the page
    cursor.execute("""
        SELECT city_id, city_name FROM cities
        WHERE province_id = %s ORDER BY city_name
    """, (pid,))
    cities = cursor.fetchall()

    cursor.execute("""
        SELECT na_id, na_number, na_name FROM constituencies_na
        WHERE province_id = %s ORDER BY na_number
    """, (pid,))
    na_list = cursor.fetchall()

    cursor.execute("""
        SELECT pa_id, pa_number, pa_name FROM constituencies_pa
        WHERE province_id = %s ORDER BY pa_number
    """, (pid,))
    pa_list = cursor.fetchall()

    if request.method == 'GET':
        cursor.close(); db.close()
        return render_template('provincial/import_voters.html',
            username=session['username'],
            cities=cities,
            na_list=na_list,
            pa_list=pa_list,
            results=None
        )

    # ── POST — process uploaded file ────────────────────────────────
    if 'csv_file' not in request.files or request.files['csv_file'].filename == '':
        cursor.close(); db.close()
        return render_template('provincial/import_voters.html',
            username=session['username'],
            cities=cities, na_list=na_list, pa_list=pa_list,
            results={'error': 'No file uploaded.'}
        )

    file    = request.files['csv_file']
    content = file.read().decode('utf-8-sig')   # utf-8-sig handles Excel BOM
    reader  = csv.DictReader(io.StringIO(content))

    # Validate headers
    required_cols = {'cnic', 'full_name', 'date_of_birth',
                     'gender', 'city_id', 'constituency_na_id'}
    if not required_cols.issubset(set(reader.fieldnames or [])):
        missing = required_cols - set(reader.fieldnames or [])
        cursor.close(); db.close()
        return render_template('provincial/import_voters.html',
            username=session['username'],
            cities=cities, na_list=na_list, pa_list=pa_list,
            results={'error': f'Missing columns: {", ".join(missing)}'}
        )

    # Get valid city/constituency IDs for this province (security check)
    cursor.execute("SELECT city_id FROM cities WHERE province_id = %s", (pid,))
    valid_cities = {str(r['city_id']) for r in cursor.fetchall()}

    cursor.execute("SELECT na_id FROM constituencies_na WHERE province_id = %s", (pid,))
    valid_na = {str(r['na_id']) for r in cursor.fetchall()}

    cursor.execute("SELECT pa_id FROM constituencies_pa WHERE province_id = %s", (pid,))
    valid_pa = {str(r['pa_id']) for r in cursor.fetchall()}

    inserted = 0
    skipped  = 0
    errors   = []

    for i, row in enumerate(reader, start=2):   # start=2 because row 1 is header
        cnic        = (row.get('cnic') or '').strip()
        full_name   = (row.get('full_name') or '').strip()
        dob         = (row.get('date_of_birth') or '').strip()
        gender      = (row.get('gender') or '').strip()
        city_id     = (row.get('city_id') or '').strip()
        na_id       = (row.get('constituency_na_id') or '').strip()
        pa_id       = (row.get('constituency_pa_id') or '').strip() or None

        # ── Row-level validation ─────────────────────────────────
        if not all([cnic, full_name, dob, gender, city_id, na_id]):
            errors.append(f'Row {i}: missing required fields — skipped.')
            skipped += 1
            continue

        if not validate_cnic_format(cnic):
            errors.append(f'Row {i} ({cnic}): invalid CNIC format — skipped.')
            skipped += 1
            continue

        # CNIC province must match this officer's province
        cnic_info = get_province_from_cnic(cnic)
        if cnic_info is None or cnic_info[2] != pid:
            province_name = cnic_info[1] if cnic_info else 'Unknown'
            errors.append(
                f'Row {i} ({cnic}): CNIC belongs to {province_name}, '
                f'not your province — skipped.'
            )
            skipped += 1
            continue

        if gender not in ('Male', 'Female', 'Other'):
            errors.append(f'Row {i} ({cnic}): invalid gender "{gender}" — skipped.')
            skipped += 1
            continue

        if city_id not in valid_cities:
            errors.append(f'Row {i} ({cnic}): city_id {city_id} not in your province — skipped.')
            skipped += 1
            continue

        if na_id not in valid_na:
            errors.append(f'Row {i} ({cnic}): NA constituency {na_id} not in your province — skipped.')
            skipped += 1
            continue

        if pa_id and pa_id not in valid_pa:
            errors.append(f'Row {i} ({cnic}): PA constituency {pa_id} not in your province — skipped.')
            skipped += 1
            continue

        # ── Insert ───────────────────────────────────────────────
        try:
            cursor.execute("""
                INSERT INTO voters
                    (cnic, full_name, date_of_birth, gender,
                     province_id, city_id,
                     constituency_na_id, constituency_pa_id,
                     registered_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    full_name           = VALUES(full_name),
                    date_of_birth       = VALUES(date_of_birth),
                    gender              = VALUES(gender),
                    city_id             = VALUES(city_id),
                    constituency_na_id  = VALUES(constituency_na_id),
                    constituency_pa_id  = VALUES(constituency_pa_id)
            """, (cnic, full_name, dob, gender,
                  pid, city_id, na_id, pa_id or None,
                  session['user_id']))
            inserted += 1
        except Exception as e:
            errors.append(f'Row {i} ({cnic}): DB error — {str(e)[:80]}')
            skipped += 1

    db.commit()

    log_action(cursor, session['username'],
               f'Imported {inserted} voters via CSV ({skipped} skipped)',
               'voters', None)
    db.commit()
    cursor.close(); db.close()

    results = {
        'inserted': inserted,
        'skipped':  skipped,
        'errors':   errors[:50]   # cap at 50 error lines shown
    }

    return render_template('provincial/import_voters.html',
        username=session['username'],
        cities=cities, na_list=na_list, pa_list=pa_list,
        results=results
    )

#-----------
@provincial_bp.route('/provincial/voters/sample-csv')
@provincial_required
def sample_voter_csv():
    """
    Generates and downloads a blank sample CSV with the correct headers
    and two example rows so the officer knows the exact format expected.
    """
    import csv, io
    from datetime import date

    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    # Pull real IDs from this province so the sample rows are valid
    cursor.execute("""
        SELECT city_id, city_name FROM cities
        WHERE province_id = %s ORDER BY city_name LIMIT 1
    """, (pid,))
    sample_city = cursor.fetchone()

    cursor.execute("""
        SELECT na_id, na_number FROM constituencies_na
        WHERE province_id = %s ORDER BY na_number LIMIT 1
    """, (pid,))
    sample_na = cursor.fetchone()

    cursor.execute("""
        SELECT pa_id, pa_number FROM constituencies_pa
        WHERE province_id = %s ORDER BY pa_number LIMIT 1
    """, (pid,))
    sample_pa = cursor.fetchone()

    cursor.close(); db.close()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row — exactly what the import route expects
    writer.writerow([
        'cnic', 'full_name', 'date_of_birth',
        'gender', 'city_id', 'constituency_na_id', 'constituency_pa_id'
    ])

    # Two example rows using real IDs from this province
    c_id  = sample_city['city_id']  if sample_city else 'YOUR_CITY_ID'
    na_id = sample_na['na_id']      if sample_na   else 'YOUR_NA_ID'
    pa_id = sample_pa['pa_id']      if sample_pa   else 'YOUR_PA_ID'

    writer.writerow([
        '35202-1234567-1', 'Muhammad Ali Khan', '1985-06-15',
        'Male', c_id, na_id, pa_id
    ])
    writer.writerow([
        '35202-7654321-3', 'Fatima Zahra', '1992-03-22',
        'Female', c_id, na_id, pa_id
    ])

    output.seek(0)
    filename = f"voter_import_template_{date.today().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )
#-------


@provincial_bp.route('/provincial/voters/export')
@provincial_required
def export_voters_csv():
    import csv, io
    from datetime import date

    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    search_cnic   = request.args.get('cnic', '').strip()
    gender_filter = request.args.get('gender', '').strip()
    city_filter   = request.args.get('city_id', '').strip()
    voted_filter  = request.args.get('voted', '').strip()

    query = """
        SELECT v.cnic, v.full_name, v.gender, v.date_of_birth,
               c.city_name, cn.na_number, cp.pa_number,
               CASE WHEN v.has_voted=1 THEN 'Voted' ELSE 'Pending' END AS status
        FROM voters v
        JOIN cities c ON v.city_id = c.city_id
        JOIN constituencies_na cn ON v.constituency_na_id = cn.na_id
        LEFT JOIN constituencies_pa cp ON v.constituency_pa_id = cp.pa_id
        WHERE v.province_id = %s
    """
    params = [pid]

    if search_cnic:
        query += " AND v.cnic LIKE %s"; params.append(f'%{search_cnic}%')
    if gender_filter:
        query += " AND v.gender = %s"; params.append(gender_filter)
    if city_filter:
        query += " AND v.city_id = %s"; params.append(city_filter)
    if voted_filter == '1':
        query += " AND v.has_voted = 1"
    elif voted_filter == '0':
        query += " AND v.has_voted = 0"

    query += " ORDER BY v.full_name ASC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close(); db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['CNIC','Full Name','Gender','Date of Birth',
                     'City','NA Seat','PA Seat','Status'])
    for r in rows:
        writer.writerow([r['cnic'], r['full_name'], r['gender'],
                         r['date_of_birth'], r['city_name'],
                         r['na_number'], r['pa_number'] or '—', r['status']])
    output.seek(0)

    filename = f"voters_{date.today().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})

# ── Polling Stations ───────────────────────────────────────────────────────

@provincial_bp.route('/provincial/stations')
@provincial_required
def stations():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    cursor.execute("""
        SELECT ps.station_id, ps.station_name, ps.address, ps.is_active,
               c.city_name,
               po.full_name AS officer_name
        FROM polling_stations ps
        JOIN cities c ON ps.city_id = c.city_id
        LEFT JOIN polling_officers po
            ON ps.station_id = po.station_id AND po.is_active = 1
        WHERE ps.province_id = %s
        ORDER BY ps.created_at DESC
    """, (pid,))
    all_stations = cursor.fetchall()

    cursor.execute("""
        SELECT city_id, city_name FROM cities
        WHERE province_id = %s ORDER BY city_name
    """, (pid,))
    cities = cursor.fetchall()

    cursor.close(); db.close()

    return render_template('provincial/stations.html',
        username=session['username'],
        stations=all_stations,
        cities=cities
    )


@provincial_bp.route('/provincial/stations/add', methods=['POST'])
@provincial_required
def add_station():
    station_name = request.form['station_name'].strip()
    address      = request.form['address'].strip()
    city_id      = request.form['city_id']
    pid          = session['province_id']

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT province_id FROM cities WHERE city_id = %s", (city_id,))
    city = cursor.fetchone()
    if not city or city['province_id'] != pid:
        flash('Invalid city.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.stations'))

    cursor.execute("""
        SELECT station_id FROM polling_stations
        WHERE station_name = %s AND city_id = %s
    """, (station_name, city_id))
    if cursor.fetchone():
        flash('A station with this name already exists in that city.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.stations'))

    cursor.execute("""
        INSERT INTO polling_stations (station_name, address, city_id, province_id, created_by_officer)
        VALUES (%s, %s, %s, %s, %s)
    """, (station_name, address, city_id, pid, session['user_id']))
    db.commit()
    sid = cursor.lastrowid

    log_action(cursor, session['username'],
               f'Added polling station: {station_name}', 'polling_stations', sid)
    db.commit()

    cursor.close(); db.close()
    flash(f'Station "{station_name}" added.', 'success')
    return redirect(url_for('provincial.stations'))


@provincial_bp.route('/provincial/stations/delete/<int:station_id>', methods=['POST'])
@provincial_required
def delete_station(station_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT station_name, province_id FROM polling_stations WHERE station_id = %s
    """, (station_id,))
    station = cursor.fetchone()

    if not station or station['province_id'] != session['province_id']:
        flash('Station not found or access denied.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.stations'))

    cursor.execute("""
        SELECT po_id FROM polling_officers
        WHERE station_id = %s AND is_active = 1
    """, (station_id,))
    if cursor.fetchone():
        flash('Cannot delete — station has an active Polling Officer. Deactivate the officer first.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.stations'))

    cursor.execute("DELETE FROM polling_stations WHERE station_id = %s", (station_id,))
    db.commit()

    log_action(cursor, session['username'],
               f"Deleted station: {station['station_name']}", 'polling_stations', station_id)
    db.commit()

    cursor.close(); db.close()
    flash('Station deleted.', 'success')
    return redirect(url_for('provincial.stations'))


# ── Polling Officers ───────────────────────────────────────────────────────

@provincial_bp.route('/provincial/polling-officers')
@provincial_required
def polling_officers():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    pid = session['province_id']

    cursor.execute("""
        SELECT po.po_id, po.full_name, po.username, po.is_active, po.last_login,
               ps.station_name
        FROM polling_officers po
        JOIN polling_stations ps ON po.station_id = ps.station_id
        WHERE po.province_id = %s
        ORDER BY po.created_at DESC
    """, (pid,))
    all_pos = cursor.fetchall()

    # Stations in this province with no active officer yet
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
    """, (pid, pid))
    available_stations = cursor.fetchall()

    cursor.close(); db.close()

    return render_template('provincial/polling_officers.html',
        username=session['username'],
        polling_officers=all_pos,
        available_stations=available_stations
    )


@provincial_bp.route('/provincial/polling-officers/add', methods=['POST'])
@provincial_required
def add_polling_officer():
    full_name  = request.form['full_name'].strip()
    username   = request.form['username'].strip()
    password   = request.form['password']
    confirm    = request.form['confirm_password']
    station_id = request.form['station_id']
    pid        = session['province_id']

    if password != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('provincial.polling_officers'))

    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('provincial.polling_officers'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT po_id FROM polling_officers WHERE username = %s", (username,))
    if cursor.fetchone():
        flash('Username already taken.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    cursor.execute("SELECT province_id FROM polling_stations WHERE station_id = %s", (station_id,))
    st = cursor.fetchone()
    if not st or st['province_id'] != pid:
        flash('Invalid station selected.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    cursor.execute("""
        SELECT po_id FROM polling_officers
        WHERE station_id = %s AND is_active = 1
    """, (station_id,))
    if cursor.fetchone():
        flash('This station already has an active officer.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    cursor.execute("""
        INSERT INTO polling_officers
            (full_name, username, password_hash, station_id, province_id, created_by_officer)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (full_name, username, hash_password(password), station_id, pid, session['user_id']))
    db.commit()
    po_id = cursor.lastrowid

    log_action(cursor, session['username'],
               f'Added Polling Officer: {full_name} ({username})', 'polling_officers', po_id)
    db.commit()

    cursor.close(); db.close()
    flash(f'Polling Officer {full_name} added.', 'success')
    return redirect(url_for('provincial.polling_officers'))


@provincial_bp.route('/provincial/polling-officers/toggle/<int:po_id>', methods=['POST'])
@provincial_required
def toggle_polling_officer(po_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username, is_active, province_id FROM polling_officers WHERE po_id = %s
    """, (po_id,))
    officer = cursor.fetchone()

    if not officer or officer['province_id'] != session['province_id']:
        flash('Officer not found or access denied.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    new_status = 0 if officer['is_active'] else 1
    word = 'Deactivated' if new_status == 0 else 'Activated'

    cursor.execute("UPDATE polling_officers SET is_active = %s WHERE po_id = %s", (new_status, po_id))
    db.commit()

    log_action(cursor, session['username'],
               f"{word} PO: {officer['full_name']} ({officer['username']})",
               'polling_officers', po_id)
    db.commit()

    cursor.close(); db.close()
    flash(f'Officer {word.lower()}.', 'success')
    return redirect(url_for('provincial.polling_officers'))


@provincial_bp.route('/provincial/polling-officers/reset-password/<int:po_id>', methods=['POST'])
@provincial_required
def reset_po_password(po_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username, province_id FROM polling_officers WHERE po_id = %s
    """, (po_id,))
    officer = cursor.fetchone()

    if not officer or officer['province_id'] != session['province_id']:
        flash('Officer not found.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    new_pw = generate_password(10)
    cursor.execute("UPDATE polling_officers SET password_hash = %s WHERE po_id = %s",
                   (hash_password(new_pw), po_id))
    db.commit()

    log_action(cursor, session['username'],
               f"Reset password for PO: {officer['full_name']} ({officer['username']})",
               'polling_officers', po_id)
    db.commit()

    cursor.close(); db.close()
    flash(
        f"Password reset for {officer['full_name']}. "
        f"New password: {new_pw} — Note this down, it won't be shown again.",
        'password'
    )
    return redirect(url_for('provincial.polling_officers'))


@provincial_bp.route('/provincial/polling-officers/delete/<int:po_id>', methods=['POST'])
@provincial_required
def delete_polling_officer(po_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, username, province_id FROM polling_officers WHERE po_id = %s
    """, (po_id,))
    officer = cursor.fetchone()

    if not officer or officer['province_id'] != session['province_id']:
        flash('Officer not found.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    cursor.execute("SELECT COUNT(*) AS c FROM voter_status WHERE po_approved_by = %s", (po_id,))
    if cursor.fetchone()['c'] > 0:
        flash('Cannot delete — officer has voter approval records linked.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('provincial.polling_officers'))

    cursor.execute("DELETE FROM polling_officers WHERE po_id = %s", (po_id,))
    db.commit()

    log_action(cursor, session['username'],
               f"Deleted PO: {officer['full_name']} ({officer['username']})",
               'polling_officers', po_id)
    db.commit()

    cursor.close(); db.close()
    flash('Polling Officer deleted.', 'success')
    return redirect(url_for('provincial.polling_officers'))