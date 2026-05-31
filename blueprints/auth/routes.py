from flask import Blueprint, render_template, request, session, redirect, url_for
from database.db import get_db
import hashlib

auth_bp = Blueprint('auth', __name__)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def write_login_log(cursor, username, role, status):
    """Write a record to login_logs on every login attempt."""
    ip = request.remote_addr or '0.0.0.0'
    cursor.execute("""
        INSERT INTO login_logs (username, role, ip_address, status)
        VALUES (%s, %s, %s, %s)
    """, (username, role, ip, status))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = hash_password(request.form['password'])

        db = get_db()
        cursor = db.cursor(dictionary=True)

        # ── Check ECP ──────────────────────────────────────────────
        cursor.execute(
            "SELECT * FROM ecp_admin WHERE username=%s AND password_hash=%s",
            (username, password)
        )
        user = cursor.fetchone()
        if user:
            session['user_id']  = user['ecp_id']
            session['username'] = user['username']
            session['role']     = 'ECP'
            write_login_log(cursor, username, 'ECP', 'Success')
            db.commit()
            cursor.close()
            db.close()
            return redirect(url_for('ecp.dashboard'))

        # ── Check Provincial Officer ────────────────────────────────
        cursor.execute(
            "SELECT * FROM provincial_officers WHERE username=%s AND password_hash=%s",
            (username, password)
        )
        user = cursor.fetchone()
        if user:
            if not user['is_active']:
                write_login_log(cursor, username, 'Provincial Officer', 'Failed')
                db.commit()
                cursor.close()
                db.close()
                return render_template('auth/login.html',
                    error="Your account has been deactivated. Contact ECP.")

            session['user_id']    = user['officer_id']
            session['username']   = user['username']
            session['role']       = 'Provincial Officer'
            session['province_id'] = user['province_id']

            # Update last_login
            cursor.execute(
                "UPDATE provincial_officers SET last_login = NOW() WHERE officer_id = %s",
                (user['officer_id'],)
            )
            write_login_log(cursor, username, 'Provincial Officer', 'Success')
            db.commit()
            cursor.close()
            db.close()
            return redirect(url_for('provincial.dashboard'))

        # ── Check Polling Officer ───────────────────────────────────
        cursor.execute(
            "SELECT * FROM polling_officers WHERE username=%s AND password_hash=%s",
            (username, password)
        )
        user = cursor.fetchone()
        if user:
            session['user_id']   = user['po_id']
            session['username']  = user['username']
            session['role']      = 'Polling Officer'
            session['station_id'] = user['station_id']
            write_login_log(cursor, username, 'Polling Officer', 'Success')
            db.commit()
            cursor.close()
            db.close()
            return redirect(url_for('polling_officer.verify'))

        # ── No match — failed login ─────────────────────────────────
        write_login_log(cursor, username, 'Unknown', 'Failed')
        db.commit()
        cursor.close()
        db.close()
        return render_template('auth/login.html',
            error="Invalid username or password.")

    return render_template('auth/login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))