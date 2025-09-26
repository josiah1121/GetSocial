from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import datetime
import uuid
import json
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Change this to a secure random key in production
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///socialgit.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    deadline_days = db.Column(db.Integer, default=7)
    active_workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'), nullable=True)
    approvers = db.relationship('User', secondary='client_approvers', backref='clients')
    active_workflow = db.relationship('Workflow', foreign_keys=[active_workflow_id])

client_approvers = db.Table('client_approvers',
    db.Column('client_id', db.Integer, db.ForeignKey('client.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

class Post(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    schedule_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='draft')  # draft, pending, approved, queued, posted
    approvals = db.relationship('Approval', backref='post', lazy=True)
    client = db.relationship('Client', backref='posts')

class Approval(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String(36), db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    user = db.relationship('User', backref='approvals')

class Workflow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    components = db.Column(db.Text, nullable=False)  # JSON string of components

# Helper to check if user is logged in
def login_required(f):
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

# Apply workflow actions to a post
def apply_workflow(post, workflow):
    if not workflow:
        app.logger.info("No active workflow for post")
        return
    components = json.loads(workflow.components)
    app.logger.info(f"Applying workflow with components: {components}")
    for comp in components:
        if comp['type'] == 'action':
            if comp['name'] == 'Assign Approvers' and comp['options']:
                approver_usernames = next((opt['value'] for opt in comp['options'] if opt['name'] == 'approvers'), [])
                app.logger.info(f"Assigning approvers: {approver_usernames}")
                for username in approver_usernames:
                    user = User.query.filter_by(username=username).first()
                    if user and user not in post.client.approvers:
                        post.client.approvers.append(user)
                    approval = Approval.query.filter_by(post_id=post.id, user_id=user.id).first()
                    if not approval:
                        approval = Approval(post_id=post.id, user_id=user.id, status='pending')
                        db.session.add(approval)
                        app.logger.info(f"Added approval for {username}")
            elif comp['name'] == 'Set Deadline' and comp['options']:
                days = next((int(opt['value']) for opt in comp['options'] if opt['name'] == 'days'), 7)
                post.schedule_date = (post.created_at + datetime.timedelta(days=days)).date()
            elif comp['name'] == 'Queue Post':
                post.status = 'queued'
            elif comp['name'] == 'Post Now':
                post.status = 'posted'  # Simulated; add real API call here
    db.session.commit()

# Routes
@app.route('/')
def index():
    app.logger.info("Accessing index route")
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username already exists.')
            return redirect(url_for('register'))
        hashed = generate_password_hash(password)
        user = User(username=username, password_hash=hashed)
        db.session.add(user)
        db.session.commit()
        flash('Registered successfully. Please log in.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/clear_session')
def clear_session():
    session.clear()
    app.logger.info("Session cleared")
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    clients = Client.query.all()
    posts = Post.query.all()
    return render_template('dashboard.html', clients=clients, posts=posts)

@app.route('/clients', methods=['GET', 'POST'])
@login_required
def clients():
    if request.method == 'POST':
        name = request.form['name']
        deadline_days = int(request.form['deadline_days'])
        approver_ids = request.form.getlist('approvers')
        if Client.query.filter_by(name=name).first():
            flash('Client already exists.')
            return redirect(url_for('clients'))
        client = Client(name=name, deadline_days=deadline_days)
        for aid in approver_ids:
            user = User.query.get(int(aid))
            if user:
                client.approvers.append(user)
        db.session.add(client)
        db.session.commit()
        flash('Client added.')
        return redirect(url_for('clients'))
    users = User.query.all()
    client_list = Client.query.all()
    return render_template('clients.html', users=users, clients=client_list)

@app.route('/clients/<int:client_id>/posts', methods=['GET', 'POST'])
@login_required
def client_posts(client_id):
    client = Client.query.get_or_404(client_id)
    if request.method == 'POST':
        content = request.form['content']
        schedule_date_str = request.form.get('schedule_date')
        schedule_date = datetime.datetime.strptime(schedule_date_str, '%Y-%m-%d').date() if schedule_date_str else None
        post = Post(client_id=client_id, content=content, schedule_date=schedule_date, status='pending')
        db.session.add(post)
        # Apply active workflow if exists
        if client.active_workflow:
            apply_workflow(post, client.active_workflow)
        else:
            # Default behavior: assign client approvers
            for approver in client.approvers:
                approval = Approval(post_id=post.id, user_id=approver.id)
                db.session.add(approval)
        db.session.commit()
        flash('Post created.')
        return redirect(url_for('client_posts', client_id=client_id))
    posts = Post.query.filter_by(client_id=client_id).all()
    return render_template('client_posts.html', client=client, posts=posts)

@app.route('/posts/<post_id>', methods=['GET', 'POST'])
@login_required
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    current_user = User.query.get(session['user_id'])
    if request.method == 'POST':
        action = request.form['action']
        approval = Approval.query.filter_by(post_id=post_id, user_id=current_user.id).first()
        if not approval:
            flash('You are not an approver for this post.')
            return redirect(url_for('post_detail', post_id=post_id))
        if action == 'approve':
            approval.status = 'approved'
        elif action == 'reject':
            approval.status = 'rejected'
        db.session.commit()
        # Check if all approved
        all_approvals = Approval.query.filter_by(post_id=post_id).all()
        if all(a.status == 'approved' for a in all_approvals):
            post.status = 'approved'
            # Apply workflow if "Post Approved" trigger exists
            workflow = post.client.active_workflow
            if workflow and any(comp['name'] == 'Post Approved' for comp in json.loads(workflow.components)):
                apply_workflow(post, workflow)
            db.session.commit()
        flash('Approval updated.')
        return redirect(url_for('post_detail', post_id=post_id))
    return render_template('post_detail.html', post=post, current_user=current_user)

@app.route('/queue', methods=['GET', 'POST'])
@login_required
def queue():
    if request.method == 'POST':
        post_id = request.form['post_id']
        action = request.form['action']
        post = Post.query.get_or_404(post_id)
        if action == 'queue':
            if post.status != 'approved':
                flash('Post not approved.')
                return redirect(url_for('queue'))
            if post.schedule_date and post.schedule_date < datetime.date.today():
                flash('Deadline passed.')
                return redirect(url_for('queue'))
            post.status = 'queued'
            db.session.commit()
            flash('Post queued.')
        elif action == 'post_now':
            if post.status != 'queued':
                flash('Post not queued.')
                return redirect(url_for('queue'))
            post.status = 'posted'
            db.session.commit()
            flash('Post posted (simulated API call).')
        return redirect(url_for('queue'))
    queued_posts = Post.query.filter_by(status='queued').all()
    approved_posts = Post.query.filter_by(status='approved').all()
    return render_template('queue.html', queued_posts=queued_posts, approved_posts=approved_posts)

@app.route('/workflow_canvas/<int:client_id>')
@login_required
def workflow_canvas(client_id):
    client = Client.query.get_or_404(client_id)
    posts = Post.query.filter_by(client_id=client_id).all()
    workflows = Workflow.query.filter_by(client_id=client_id).all()
    return render_template('workflow_canvas.html', client=client, posts=posts, workflows=workflows)

@app.route('/save_workflow/<int:client_id>', methods=['POST'])
@login_required
def save_workflow(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json()
    workflow = Workflow(client_id=client_id, name=data['name'], components=json.dumps(data['components']))
    db.session.add(workflow)
    db.session.commit()
    return jsonify({'status': 'success', 'workflow_id': workflow.id})

@app.route('/set_active_workflow/<int:client_id>/<int:workflow_id>', methods=['POST'])
@login_required
def set_active_workflow(client_id, workflow_id):
    client = Client.query.get_or_404(client_id)
    if workflow_id == 0:
        client.active_workflow_id = None
    else:
        workflow = Workflow.query.get_or_404(workflow_id)
        if workflow.client_id != client_id:
            return jsonify({'status': 'error', 'message': 'Workflow does not belong to this client'}), 403
        client.active_workflow_id = workflow_id
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/load_workflow/<int:workflow_id>')
@login_required
def load_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    return jsonify({'name': workflow.name, 'components': json.loads(workflow.components)})

if __name__ == '__main__':
    if not os.path.exists('socialgit.db'):
        with app.app_context():
            db.create_all()
    app.run(debug=True)