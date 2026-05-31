from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from database.db import get_db
from .cnic_utils import get_province_from_cnic, validate_cnic_format

voter_bp = Blueprint('voter', __name__)


@voter_bp.route('/vote/login', methods=['GET', 'POST'])
def vote_login():
    """
    Voter Machine login screen.
    Checks: CNIC format → province match → DB lookup → not voted → pending insert
    """
    error = None

    if request.method == 'POST':
        entered_name = request.form['full_name'].strip()
        cnic         = request.form['cnic'].strip()
        station_id   = request.args.get('station_id') or request.form.get('station_id')

        if not station_id:
            error = 'This machine is not configured. Please call the Polling Officer.'
            return render_template('voter/login.html', error=error, station_id=None)

        # ── CHECK 1: CNIC format ────────────────────────────────────
        if not validate_cnic_format(cnic):
            error = 'Invalid CNIC format. Please enter as XXXXX-XXXXXXX-X.'
            return render_template('voter/login.html', error=error, station_id=station_id)

        # ── CHECK 2: CNIC province vs station province ──────────────
        cnic_info = get_province_from_cnic(cnic)
        if cnic_info is None:
            error = 'CNIC prefix not recognised. Please see the Polling Officer.'
            return render_template('voter/login.html', error=error, station_id=station_id)

        division_name, cnic_province_name, cnic_province_id = cnic_info

        db = get_db()
        cursor = db.cursor(dictionary=True)

        # Get the province of this polling station
        cursor.execute("""
            SELECT ps.province_id, p.province_name
            FROM polling_stations ps
            JOIN provinces p ON ps.province_id = p.province_id
            WHERE ps.station_id = %s
        """, (station_id,))
        station = cursor.fetchone()

        if not station:
            error = 'Polling station not found. Please call the Polling Officer.'
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        if cnic_province_id != station['province_id']:
            error = (
                f'Your CNIC ({division_name}) is registered in '
                f'{cnic_province_name}. This polling station is in '
                f'{station["province_name"]}. Please go to your '
                f'correct polling station.'
            )
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        # ── CHECK 3: DB lookup ──────────────────────────────────────
        cursor.execute("""
            SELECT voter_id, cnic, full_name, has_voted,
                constituency_na_id, constituency_pa_id,
                city_id
            FROM voters
            WHERE cnic = %s
        """, (cnic,))
        voter = cursor.fetchone()

        if not voter:
            error = 'CNIC not found in registered voters. Please see the Polling Officer.'
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        # ── CHECK 4: Name match ─────────────────────────────────────
        if entered_name.lower() not in voter['full_name'].lower():
            error = 'Name does not match our records. Please see the Polling Officer.'
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)


# ── CHECK 5: Already voted ──────────────────────────────────
        if voter['has_voted']:
            error = 'You have already cast your vote.'
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        # ── CHECK 6: City must match station city ───────────────────
        cursor.execute("""
            SELECT ps.city_id, c.city_name
            FROM polling_stations ps
            JOIN cities c ON ps.city_id = c.city_id
            WHERE ps.station_id = %s
        """, (station_id,))
        station_info = cursor.fetchone()

        if not station_info:
            error = 'This machine is not configured correctly. Please call the Polling Officer.'
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        if station_info['city_id'] != voter['city_id']:
            cursor.execute("SELECT city_name FROM cities WHERE city_id = %s", (voter['city_id'],))
            voter_city = cursor.fetchone()
            error = (
                f"You are registered in {voter_city['city_name']}. "
                f"You must vote at a station in {voter_city['city_name']}, "
                f"not at {station_info['city_name']}."
            )
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        # ── CHECK 7: Active election ────────────────────────────────
        
        cursor.execute("""
            SELECT election_id FROM elections
            WHERE status = 'Active' ORDER BY created_at DESC LIMIT 1
        """)
        election = cursor.fetchone()
        if not election:
            error = 'No active election at this time.'
            cursor.close(); db.close()
            return render_template('voter/login.html', error=error, station_id=station_id)

        # ── Insert pending voter_status ─────────────────────────────
        cursor.execute("""
            SELECT status_id FROM voter_status
            WHERE cnic = %s AND station_id = %s AND status IN ('pending','approved')
        """, (cnic, station_id))
        existing = cursor.fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO voter_status
                    (cnic, election_id, has_voted, status, voter_name, station_id)
                VALUES (%s, %s, %s, 'pending', %s, %s)
                ON DUPLICATE KEY UPDATE
                    status      = 'pending',
                    voter_name  = VALUES(voter_name),
                    station_id  = VALUES(station_id),
                    election_id = VALUES(election_id)
            """, (cnic, election['election_id'], 0, voter['full_name'], station_id))
            db.commit()

        cursor.close(); db.close()

        session['vm_cnic']     = cnic
        session['vm_name']     = voter['full_name']
        session['vm_station']  = int(station_id)
        session['vm_election'] = election['election_id']
        session['vm_na_id']    = voter['constituency_na_id']
        session['vm_pa_id']    = voter['constituency_pa_id']

        return redirect(url_for('voter.waiting', station_id=station_id))

    station_id = request.args.get('station_id')
    return render_template('voter/login.html', error=None, station_id=station_id)


@voter_bp.route('/vote/waiting')
def waiting():
    if not session.get('vm_cnic'):
        return redirect(url_for('voter.vote_login',
                                station_id=request.args.get('station_id')))
    station_id = session.get('vm_station') or request.args.get('station_id')
    return render_template('voter/waiting.html',
                           station_id=station_id,
                           voter_name=session.get('vm_name'))


@voter_bp.route('/vote/check-status')
def check_status():
    cnic       = session.get('vm_cnic')
    station_id = session.get('vm_station')
    if not cnic or not station_id:
        return jsonify({'status': 'error'})
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT status FROM voter_status
        WHERE cnic = %s AND station_id = %s
        ORDER BY status_id DESC LIMIT 1
    """, (cnic, station_id))
    row = cursor.fetchone()
    cursor.close(); db.close()
    if not row:
        return jsonify({'status': 'pending'})
    return jsonify({'status': row['status']})


@voter_bp.route('/vote/ballot')
def ballot():
    cnic       = session.get('vm_cnic')
    station_id = session.get('vm_station')
    na_id      = session.get('vm_na_id')
    pa_id      = session.get('vm_pa_id')
    if not cnic:
        return redirect(url_for('voter.vote_login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT status FROM voter_status
        WHERE cnic = %s AND station_id = %s
        ORDER BY status_id DESC LIMIT 1
    """, (cnic, station_id))
    vs = cursor.fetchone()
    if not vs or vs['status'] != 'approved':
        cursor.close(); db.close()
        return redirect(url_for('voter.waiting'))

    cursor.execute("""
        SELECT c.candidate_id, c.full_name, c.photo_path,
               p.party_name, p.abbreviation, p.party_color
        FROM candidate_na cna
        JOIN candidates c ON cna.candidate_id = c.candidate_id
        JOIN parties p ON c.party_id = p.party_id
        WHERE cna.na_id = %s AND cna.election_id = %s AND c.is_active = 1
        ORDER BY c.full_name
    """, (na_id, session['vm_election']))
    na_candidates = cursor.fetchall()

    pa_candidates = []
    if pa_id:
        cursor.execute("""
            SELECT c.candidate_id, c.full_name, c.photo_path,
                   p.party_name, p.abbreviation, p.party_color
            FROM candidate_pa cpa
            JOIN candidates c ON cpa.candidate_id = c.candidate_id
            JOIN parties p ON c.party_id = p.party_id
            WHERE cpa.pa_id = %s AND cpa.election_id = %s AND c.is_active = 1
            ORDER BY c.full_name
        """, (pa_id, session['vm_election']))
        pa_candidates = cursor.fetchall()

    cursor.close(); db.close()

    return render_template('voter/ballot.html',
        voter_name=session.get('vm_name'),
        cnic=cnic,
        na_candidates=na_candidates,
        pa_candidates=pa_candidates
    )


@voter_bp.route('/vote/cast', methods=['POST'])
def cast_vote():
    cnic        = session.get('vm_cnic')
    station_id  = session.get('vm_station')
    election_id = session.get('vm_election')
    na_id       = session.get('vm_na_id')
    pa_id       = session.get('vm_pa_id')
    na_candidate = request.form.get('na_candidate')
    pa_candidate = request.form.get('pa_candidate')

    if not cnic or not na_candidate:
        return redirect(url_for('voter.ballot'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT has_voted FROM voters WHERE cnic = %s", (cnic,))
    voter = cursor.fetchone()
    if not voter or voter['has_voted']:
        cursor.close(); db.close()
        session.clear()
        return redirect(url_for('voter.vote_login', station_id=station_id))

    cursor.execute("""
        SELECT status FROM voter_status
        WHERE cnic = %s AND station_id = %s
        ORDER BY status_id DESC LIMIT 1
    """, (cnic, station_id))
    vs = cursor.fetchone()
    if not vs or vs['status'] != 'approved':
        cursor.close(); db.close()
        return redirect(url_for('voter.waiting'))

    import secrets
    ballot_token = secrets.token_hex(32)

    cursor.execute("""
        INSERT INTO ballots (ballot_token, election_id, station_id)
        VALUES (%s, %s, %s)
    """, (ballot_token, election_id, station_id))
    db.commit()
    ballot_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO votes_na (ballot_id, candidate_id, na_id, election_id)
        VALUES (%s, %s, %s, %s)
    """, (ballot_id, na_candidate, na_id, election_id))

    if pa_candidate and pa_id:
        cursor.execute("""
            INSERT INTO votes_pa (ballot_id, candidate_id, pa_id, election_id)
            VALUES (%s, %s, %s, %s)
        """, (ballot_id, pa_candidate, pa_id, election_id))

    cursor.execute("UPDATE voters SET has_voted = 1 WHERE cnic = %s", (cnic,))
    cursor.execute("""
        UPDATE voter_status
        SET status = 'voted', has_voted = 1, voted_at = NOW()
        WHERE cnic = %s AND station_id = %s
    """, (cnic, station_id))
    db.commit()
    cursor.close(); db.close()

    # Clear voter session but keep station_id for next voter
    session.pop('vm_cnic', None)
    session.pop('vm_name', None)
    session.pop('vm_election', None)
    session.pop('vm_na_id', None)
    session.pop('vm_pa_id', None)

    return redirect(url_for('voter.thankyou',
                            station_id=session.get('vm_station')))


@voter_bp.route('/vote/thankyou')
def thankyou():
    # Pass station_id through so "Next Voter" button works correctly
    station_id = request.args.get('station_id') or session.pop('vm_station', 1)
    return render_template('voter/thankyou.html', station_id=station_id)