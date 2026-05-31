from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from database.db import get_db
from functools import wraps

po_bp = Blueprint('polling_officer', __name__)

def po_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'Polling Officer':
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@po_bp.route('/po/verify')
@po_required
def verify():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    station_id = session['station_id']

    # Station name for display
    cursor.execute("""
        SELECT ps.station_name, c.city_name
        FROM polling_stations ps
        JOIN cities c ON ps.city_id = c.city_id
        WHERE ps.station_id = %s
    """, (station_id,))
    station = cursor.fetchone()

    # Recent approvals/rejections at this station (last 8)
    cursor.execute("""
        SELECT vs.cnic, vs.voter_name, vs.status, vs.voted_at,
               v.full_name
        FROM voter_status vs
        LEFT JOIN voters v ON vs.cnic = v.cnic
        WHERE vs.station_id = %s
          AND vs.status IN ('approved','rejected','voted')
        ORDER BY vs.voted_at DESC
        LIMIT 8
    """, (station_id,))
    recent = cursor.fetchall()

    # Stats for this station today
    cursor.execute("""
        SELECT
            COUNT(CASE WHEN status='voted' THEN 1 END)    AS voted_today,
            COUNT(CASE WHEN status='rejected' THEN 1 END) AS rejected_today
        FROM voter_status
        WHERE station_id = %s
          AND DATE(voted_at) = CURDATE()
    """, (station_id,))
    stats = cursor.fetchone()

    cursor.close(); db.close()

    return render_template('polling_officer/verify.html',
        username=session['username'],
        station=station,
        recent=recent,
        stats=stats
    )


@po_bp.route('/po/pending-voter')
@po_required
def pending_voter():
    """
    JSON endpoint — called every 2s by the PO verify screen.
    Returns the oldest pending voter at this station, or null.
    """
    db = get_db()
    cursor = db.cursor(dictionary=True)
    station_id = session['station_id']

    cursor.execute("""
        SELECT vs.cnic, vs.voter_name, vs.status_id,
               v.full_name, v.gender, v.constituency_na_id, v.constituency_pa_id,
               cn.na_number, cp.pa_number, c.city_name,
               v.has_voted
        FROM voter_status vs
        JOIN voters v ON vs.cnic = v.cnic
        JOIN constituencies_na cn ON v.constituency_na_id = cn.na_id
        LEFT JOIN constituencies_pa cp ON v.constituency_pa_id = cp.pa_id
        JOIN cities c ON v.city_id = c.city_id
        WHERE vs.station_id = %s
          AND vs.status = 'pending'
        ORDER BY vs.status_id ASC
        LIMIT 1
    """, (station_id,))
    voter = cursor.fetchone()
    cursor.close(); db.close()

    if not voter:
        return jsonify({'pending': False})

    return jsonify({
        'pending':    True,
        'cnic':       voter['cnic'],
        'name':       voter['full_name'],
        'gender':     voter['gender'],
        'city':       voter['city_name'],
        'na':         voter['na_number'],
        'pa':         voter['pa_number'] or '—',
        'has_voted':  bool(voter['has_voted']),
        'status_id':  voter['status_id']
    })


@po_bp.route('/po/approve/<cnic>', methods=['POST'])
@po_required
def approve_voter(cnic):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    station_id = session['station_id']

    # Confirm this pending request belongs to this station
    cursor.execute("""
        SELECT status_id, status FROM voter_status
        WHERE cnic = %s AND station_id = %s AND status = 'pending'
    """, (cnic, station_id))
    vs = cursor.fetchone()

    if not vs:
        cursor.close(); db.close()
        return jsonify({'ok': False, 'error': 'No pending request found'})

    cursor.execute("""
        UPDATE voter_status
        SET status = 'approved', po_approved_by = %s
        WHERE cnic = %s AND station_id = %s AND status = 'pending'
    """, (session['user_id'], cnic, station_id))
    db.commit()

    cursor.close(); db.close()
    return jsonify({'ok': True})


@po_bp.route('/po/reject/<cnic>', methods=['POST'])
@po_required
def reject_voter(cnic):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    station_id = session['station_id']

    cursor.execute("""
        UPDATE voter_status
        SET status = 'rejected'
        WHERE cnic = %s AND station_id = %s AND status = 'pending'
    """, (cnic, station_id))
    db.commit()

    cursor.close(); db.close()
    return jsonify({'ok': True})