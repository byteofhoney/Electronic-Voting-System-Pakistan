from flask import Flask, redirect, url_for
from config import Config
from blueprints.auth.routes import auth_bp
from blueprints.ecp.routes import ecp_bp
from blueprints.provincial.routes import provincial_bp
from blueprints.polling_officer.routes import po_bp
from blueprints.voter.routes import voter_bp
import os

app = Flask(__name__)
app.config.from_object(Config)

app.config['UPLOAD_FOLDER'] = os.path.join('static', 'images', 'uploads', 'parties')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

app.secret_key = os.getenv('SECRET_KEY')

app.register_blueprint(auth_bp)
app.register_blueprint(ecp_bp)
app.register_blueprint(provincial_bp)
app.register_blueprint(po_bp)
app.register_blueprint(voter_bp)

@app.route('/')
def index():
    return redirect(url_for('auth.login'))

if __name__ == '__main__':
    app.run(debug=True)

    