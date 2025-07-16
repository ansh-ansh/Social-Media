from flask import Flask, render_template, request, redirect, url_for, flash, session, g, jsonify, make_response
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField, FileField, HiddenField
from wtforms.validators import DataRequired, Optional, Length
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import sqlite3
import os
import random
import sys
from dateutil.relativedelta import relativedelta
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask import send_from_directory # Added for serving uploaded files


# --- App Setup ---
app = Flask(__name__)
# IMPORTANT: Replace with a strong, unique, and random key in production!
app.secret_key = os.environ.get('SECRET_KEY', 'a_fallback_secret_key_for_dev_only_replace_this_in_prod')

# --- Session Lifetime ---
app.permanent_session_lifetime = timedelta(days=365) # Make session last for 1 year (or any duration you want)

# Initialize CSRFProtect *after* app.secret_key is set
csrf = CSRFProtect(app)
socketio = SocketIO(app)

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'ogg'} # Added video extensions
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload size

DATABASE_FILE = 'database.db'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- Database Setup ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row  # This makes rows behave like dictionaries
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Enable foreign key support for CASCADE deletes
    cursor.execute('PRAGMA foreign_keys = ON;')

    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            anon_id TEXT PRIMARY KEY,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_sigma INTEGER DEFAULT 0
        )
    ''')

    # Posts Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, -- Display name, e.g., AnonXXXX
            content TEXT, -- Main text content/description of the post
            title TEXT, -- Optional title for text posts/discussions
            image_filename TEXT, -- Path to image/video file
            original_poster_anon_id TEXT NOT NULL,
            sigma INTEGER DEFAULT 0,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (original_poster_anon_id) REFERENCES users (anon_id) ON DELETE CASCADE
        )
    ''')

    # Comments Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            commenter_anon_id TEXT NOT NULL,
            content TEXT NOT NULL,
            sigma INTEGER DEFAULT 0,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            FOREIGN KEY (commenter_anon_id) REFERENCES users (anon_id) ON DELETE CASCADE
        )
    ''')

    # Votes Table (for posts)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            voter_anon_id TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('up', 'down')), -- 'up' or 'down'
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, voter_anon_id), -- A user can only vote once per post
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            FOREIGN KEY (voter_anon_id) REFERENCES users (anon_id) ON DELETE CASCADE
        )
    ''')

    # Comment Votes Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            voter_anon_id TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('up', 'down')), -- 'up' or 'down'
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(comment_id, voter_anon_id), -- A user can only vote once per comment
            FOREIGN KEY (comment_id) REFERENCES comments (id) ON DELETE CASCADE,
            FOREIGN KEY (voter_anon_id) REFERENCES users (anon_id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()

# Ensure the database is initialized on startup
init_db()

# --- Utility Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- UPDATED get_or_create_user_data() Function ---
def get_or_create_user_data():
    """
    Manages user session and ensures anonymous user data is in the database.
    Creates a new anonymous user if not found in session or DB.
    Returns (anon_id, join_date, total_sigma, threads_created, comments_made, total_likes_received_on_posts)
    for the current anonymous user.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    session.permanent = True # Ensure session persists across browser closures

    current_anon_id = session.get("anon_id")

    threads_created = 0
    comments_made = 0
    total_likes_received_on_posts = 0
    total_sigma = 0
    join_date = datetime.today().strftime("%Y-%m-%d %H:%M:%S") # Default if not found

    if not current_anon_id or current_anon_id == "0000": # "0000" acts as a sentinel for invalid/missing anon_id
        new_anon_id = str(random.randint(1000, 9999))
        while True: # Ensure generated ID is unique
            cursor.execute("SELECT 1 FROM users WHERE anon_id = ?", (new_anon_id,))
            if cursor.fetchone() is None:
                break
            new_anon_id = str(random.randint(1000, 9999))

        new_join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_total_sigma = 0 # Starting sigma for a brand new user

        try:
            cursor.execute("INSERT INTO users (anon_id, join_date, total_sigma) VALUES (?, ?, ?)",
                           (new_anon_id, new_join_date, new_total_sigma))
            conn.commit()
            current_anon_id = new_anon_id
            join_date = new_join_date
            total_sigma = new_total_sigma
        except sqlite3.IntegrityError:
            print(f"Error: Generated anon_id {new_anon_id} already exists (collision). Rolling back.", file=sys.stderr)
            conn.rollback()
            conn.close()
            return "0000", "N/A", 0, 0, 0, 0 # Return default if collision
        session["anon_id"] = current_anon_id
    else:
        # User has an anon_id in session, try to fetch from DB
        cursor.execute("SELECT total_sigma, join_date FROM users WHERE anon_id = ?", (current_anon_id,))
        user_data_fetched = cursor.fetchone()
        if user_data_fetched:
            total_sigma = user_data_fetched['total_sigma']
            join_date = user_data_fetched['join_date']
        else:
            # anon_id in session but not in DB (e.g., DB reset, session persisted)
            print(f"User {current_anon_id} in session but not in DB. Recreating as anonymous...", file=sys.stderr)
            session.pop("anon_id", None) # Clear invalid session anon_id
            session.pop("sigma_score", None)
            session.pop("join_date", None)
            return get_or_create_user_data() # Recurse to create a new anon user

    # Calculate user statistics based on the current_anon_id
    try:
        cursor.execute("SELECT COUNT(*) FROM posts WHERE original_poster_anon_id = ?", (current_anon_id,))
        threads_created = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM comments WHERE commenter_anon_id = ?", (current_anon_id,))
        comments_made = cursor.fetchone()[0]

        # Calculate total sigma (likes) received on posts created by this user
        cursor.execute("SELECT SUM(sigma) FROM posts WHERE original_poster_anon_id = ?", (current_anon_id,))
        total_likes_received_on_posts = cursor.fetchone()[0] or 0

    except Exception as e:
        print(f"Error calculating user stats for anon_id {current_anon_id}: {e}", file=sys.stderr)
    finally:
        conn.close()

    session["sigma_score"] = total_sigma
    session["join_date"] = join_date

    return current_anon_id, join_date, total_sigma, threads_created, comments_made, total_likes_received_on_posts

# --- Before Request Hook (User Session Management) ---
@app.before_request
def load_user_into_g():
    g.anon_id, g.join_date, g.sigma_score, g.threads_created, g.comments_made, g.total_likes_received_on_posts = get_or_create_user_data()

# --- Forms (Flask-WTF) ---
class PostForm(FlaskForm):
    title = StringField('Title', validators=[Length(max=100)])
    content = TextAreaField('Content', validators=[Optional(), Length(max=2000)])
    image = FileField('Image/Video (Optional)')
    submit = SubmitField('Create Post')

class CommentForm(FlaskForm):
    comment_content = TextAreaField('Your Comment', validators=[DataRequired(), Length(max=500)])
    submit = SubmitField('Add Comment')

class DeletePostForm(FlaskForm):
    # This form only needs CSRF protection
    submit = SubmitField('Delete Post')

class DeleteCommentForm(FlaskForm):
    # This form only needs CSRF protection
    submit = SubmitField('Delete Comment')

# --- Jinja2 Filters ---
@app.template_filter('format_time_ago')
def format_time_ago_filter(timestamp_str):
    """
    Formats a given timestamp string (e.g., "YYYY-MM-DD HH:MM:SS") into a human-readable
    "time ago" string (e.g., "30 minutes ago", "1 day ago").
    """
    if timestamp_str is None:
        return "Unknown time"

    try:
        dt_object = datetime.strptime(str(timestamp_str), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(timestamp_str)
    
    now = datetime.now()
    diff = relativedelta(now, dt_object)

    if diff.years > 0:
        return f"{diff.years} year{'s' if diff.years > 1 else ''} ago"
    elif diff.months > 0:
        return f"{diff.months} month{'s' if diff.months > 1 else ''} ago"
    elif diff.days > 0:
        if diff.days == 1:
            return "1 day ago"
        elif diff.days < 7:
            return f"{diff.days} days ago"
        else:
            weeks = diff.days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    elif diff.hours > 0:
        return f"{diff.hours} hour{'s' if diff.hours > 1 else ''} ago"
    elif diff.minutes > 0:
        return f"{diff.minutes} minute{'s' if diff.minutes > 1 else ''} ago"
    else:
        seconds = int((now - dt_object).total_seconds())
        if seconds < 0: # Future date (shouldn't happen with 'ago')
            return "Just now"
        return f"{seconds} second{'s' if seconds != 1 else ''} ago"

app.jinja_env.filters['format_time_ago'] = format_time_ago_filter


# --- Routes ---

@app.route('/')
@app.route('/feed')
def feed():
    sort = request.args.get("sort", "best")
    view = request.args.get("view", "card")
    search_query = request.args.get("q", "").strip()

    # Query to select all posts, including their original poster's anon_id
    query = "SELECT id, username, content, image_filename, created, sigma, original_poster_anon_id, title FROM posts"
    filters = []
    params = []

    if search_query:
        filters.append("(username LIKE ? OR content LIKE ? OR title LIKE ?)")
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if filters:
        query += " WHERE " + " AND ".join(filters)

    if sort == "hottest":
        query += " ORDER BY sigma DESC"
    elif sort == "best":
        query += " ORDER BY sigma DESC" # For feed, 'best' by sigma is usually hottest
    else: # latest
        query += " ORDER BY created DESC"

    posts_data = []
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        fetched_posts = cursor.fetchall()

        for post_row in fetched_posts:
            post_id = post_row['id']
            user_vote_type = None
            if g.anon_id != "0000":
                cursor.execute("SELECT type FROM votes WHERE post_id = ? AND voter_anon_id = ?", (post_id, g.anon_id))
                vote_row = cursor.fetchone()
                if vote_row:
                    user_vote_type = vote_row['type']

            # post: [id, username, content, image_filename, created, sigma, original_poster_anon_id, user_vote_status, title]
            post_list = [
                post_row['id'],
                post_row['username'],
                post_row['title'],
                post_row['content'],
                post_row['image_filename'],
                post_row['created'], # raw timestamp string, will be filtered in template
                post_row['sigma'],
                post_row['original_poster_anon_id'],
                user_vote_type,
                
            ]
            posts_data.append(tuple(post_list))

    except Exception as e:
        flash(f"Error loading posts: {str(e)}", "danger")
        posts_data = []
    finally:
        conn.close()

    delete_post_form = DeletePostForm()

    return render_template(
        "feed.html",
        posts=posts_data,
        anon_id=g.anon_id,
        join_date=g.join_date,
        sigma_score=g.sigma_score,
        threads_created=g.threads_created,
        comments_made=g.comments_made,
        total_likes_received_on_posts=g.total_likes_received_on_posts,
        sort=sort,
        view=view,
        search_query=search_query,
        delete_post_form=delete_post_form
    )

# app.py snippet for /post_detail/<int:post_id> route
@app.route('/post/<int:post_id>')
def post_detail(post_id):
    post = None
    comments_for_template = []

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Fetch post details
        cursor.execute("SELECT id, username, content, image_filename, created, sigma, original_poster_anon_id, title FROM posts WHERE id = ?", (post_id,))
        post_raw = cursor.fetchone()

        if post_raw:
            user_post_vote_type = None
            if g.anon_id != "0000":
                cursor.execute("SELECT type FROM votes WHERE post_id = ? AND voter_anon_id = ?", (post_id, g.anon_id))
                vote_row = cursor.fetchone()
                if vote_row:
                    user_post_vote_type = vote_row['type']

            # post: [id, username, content, image_filename, created, sigma, original_poster_anon_id, user_vote_status, title]
            post_list = [
                post_raw['id'],              # [0]
                post_raw['username'],        # [1]
                post_raw['content'],         # [2] - This is the main body content
                post_raw['image_filename'],  # [3]
                post_raw['created'],         # [4] - This is the timestamp
                post_raw['sigma'],           # [5]
                post_raw['original_poster_anon_id'], # [6]
                user_post_vote_type,         # [7] - User's vote type for this post
                post_raw['title']            # [8] - This is the post title
            ]
            post = tuple(post_list)

            # Fetch comments for the post
            cursor.execute("SELECT id, commenter_anon_id, content, created, sigma FROM comments WHERE post_id = ? ORDER BY created ASC", (post_id,))
            fetched_comments = cursor.fetchall()

            for comment_row in fetched_comments:
                comment_id = comment_row['id']
                user_comment_vote_type = None
                if g.anon_id != "0000":
                    cursor.execute("SELECT type FROM comment_votes WHERE comment_id = ? AND voter_anon_id = ?", (comment_id, g.anon_id))
                    comment_vote_row = cursor.fetchone()
                    if comment_vote_row:
                        user_comment_vote_type = comment_vote_row['type']

                # comment: [id, commenter_anon_id, content, sigma, user_vote_status, created]
                comment_list = [
                    comment_row['id'],
                    comment_row['commenter_anon_id'],
                    comment_row['content'],
                    comment_row['sigma'],
                    user_comment_vote_type,
                    comment_row['created']
                ]
                comments_for_template.append(tuple(comment_list))

    except Exception as e:
        flash(f"Error loading post details: {str(e)}", "danger")
        post = None
        comments_for_template = []
    finally:
        conn.close()

    if not post:
        flash("Post not found.", "danger")
        return redirect(url_for('feed'))

    comment_form = CommentForm()
    delete_post_form = DeletePostForm()
    delete_comment_form = DeleteCommentForm()

    # Determine which template to render based on post type
    # post[3] contains image_filename
    template_to_render = 'post_detail.html' # Default for threads
    if post[3]: # If image_filename is not None or empty
        template_to_render = 'post_detail_like.html' # Use the new template for photo/video posts
    print(post[2])


    return render_template(
        template_to_render, # Use the determined template
        post=post,
        comments=comments_for_template,
        anon_id=g.anon_id,
        join_date=g.join_date,
        sigma_score=g.sigma_score,
        threads_created=g.threads_created,
        comments_made=g.comments_made,
        total_likes_received_on_posts=g.total_likes_received_on_posts,
        comment_form=comment_form,
        delete_post_form=delete_post_form,
        delete_comment_form=delete_comment_form
    )


            
@app.route('/create_post', methods=['GET', 'POST'])
def create_post():
    form = PostForm()
    if form.validate_on_submit():
        title = form.title.data
        content = form.content.data
        image_file = form.image.data
        anon_id = g.anon_id
        username = f"Anon{anon_id}" # Using AnonID as display name

        image_filename = None
        if image_file and image_file.filename:
            if allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                unique_filename = f"{anon_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file_size = len(image_file.read()) # Read file size *before* saving
                image_file.seek(0) # Reset file pointer after reading size
                if file_size > app.config['MAX_CONTENT_LENGTH']:
                    flash(f'File size exceeds limit of {app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024):.0f} MB.', 'warning')
                    return render_template('create_post.html', form=form)

                image_file.save(image_path)
                image_filename = unique_filename
            else:
                flash('Image or video type not allowed. Use PNG, JPG, JPEG, GIF, MP4, WEBM, OGG.', 'warning')
                return render_template('create_post.html', form=form)

        if not content and not image_filename and not title:
            flash("Posts must have text, an image/video, or a title.", "warning")
            return render_template('create_post.html', form=form)

        try:
            conn = get_db_connection()
            conn.execute('''
                INSERT INTO posts (username, content, title, image_filename, original_poster_anon_id, sigma, created)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (username, content, title, image_filename, anon_id, 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()
            flash('Post created successfully!', 'success')
            return redirect(url_for('feed'))
        except Exception as e:
            flash(f'Failed to create post: {str(e)}', 'danger')
            return render_template('create_post.html', form=form)

    return render_template('create_post.html', form=form)

@app.route('/post/<int:post_id>/add_comment', methods=['POST'])
def add_generic_comment(post_id):
    form = CommentForm()
    if form.validate_on_submit(): # This validates the CSRF token and comment_content
        comment_content = form.comment_content.data
        anon_id = g.anon_id

        try:
            conn = get_db_connection()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute('''
                INSERT INTO comments (post_id, commenter_anon_id, content, created, sigma)
                VALUES (?, ?, ?, ?, ?)
            ''', (post_id, anon_id, comment_content, current_time, 0))
            conn.commit()
            flash('Comment added successfully!', 'success')
        except Exception as e:
            flash(f'Failed to add comment: {str(e)}', 'danger')
            print(f"Error adding comment: {e}", file=sys.stderr)
        finally:
            conn.close()
    else:
        # If validation fails (e.g., CSRF token missing/invalid, or content too long)
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {field}: {error}", 'danger')
        if not form.csrf_token.data:
             flash("CSRF token missing or invalid. Please refresh the page and try again.", 'danger')

    return redirect(url_for('post_detail', post_id=post_id))

@app.route('/delete_post/<int:post_id>', methods=['POST'])
def delete_post(post_id):
    form = DeletePostForm()
    if form.validate_on_submit(): # This validates the CSRF token
        conn = get_db_connection()
        post = conn.execute("SELECT original_poster_anon_id, image_filename FROM posts WHERE id = ?", (post_id,)).fetchone()

        if post is None:
            conn.close()
            flash("Post not found.", "danger")
            return redirect(url_for('feed'))

        # Only allow the original poster to delete
        if post['original_poster_anon_id'] != g.anon_id:
            conn.close()
            flash("You do not have permission to delete this post.", "danger")
            return redirect(url_for('post_detail', post_id=post_id))

        try:
            # Delete associated image file if exists
            if post['image_filename']:
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], post['image_filename'])
                if os.path.exists(image_path):
                    os.remove(image_path)
                    print(f"Deleted image file: {image_path}")

            # Database CASCADE deletes should handle comments and votes automatically due to FOREIGN KEY ON DELETE CASCADE
            conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            conn.commit()
            flash("Post and its associated data deleted successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error deleting post: {str(e)}", "danger")
        finally:
            conn.close()
    else:
        flash("CSRF token missing or invalid when deleting post.", "danger")
    return redirect(url_for('feed'))

@app.route('/delete_comment/<int:comment_id>', methods=['POST'])
def delete_comment(comment_id):
    form = DeleteCommentForm()
    if form.validate_on_submit(): # This validates the CSRF token
        conn = get_db_connection()
        comment = conn.execute("SELECT post_id, commenter_anon_id FROM comments WHERE id = ?", (comment_id,)).fetchone()

        if comment is None:
            conn.close()
            flash("Comment not found.", "danger")
            return redirect(request.referrer or url_for('feed'))

        # Only allow the original commenter to delete
        if comment['commenter_anon_id'] != g.anon_id:
            conn.close()
            flash("You do not have permission to delete this comment.", "danger")
            return redirect(request.referrer or url_for('post_detail', post_id=comment['post_id']))

        try:
            # Database CASCADE deletes should handle comment votes automatically
            conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            conn.commit()
            flash("Comment deleted successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error deleting comment: {str(e)}", "danger")
        finally:
            conn.close()
    else:
        flash("CSRF token missing or invalid when deleting comment.", "danger")
    return redirect(request.referrer or url_for('feed')) # Redirect back to the page they came from

@app.route('/vote', methods=['POST'])
@csrf.exempt # Exempt this route from CSRF protection for now to test if it fixes the issue
def handle_vote():
    data = request.get_json()
    if not data:
        return jsonify(success=False, message="Invalid JSON or empty request body"), 400

    item_type = data.get('item_type')
    item_id = data.get(f'{item_type}_id')
    vote_type = data.get('vote_type')
    anon_id = g.anon_id

    if item_type not in ['post', 'comment'] or not isinstance(item_id, int) or vote_type not in ['up', 'down']:
        return jsonify(success=False, message="Invalid item type, ID, or vote type provided."), 400

    if not anon_id or anon_id == "0000":
        return jsonify(success=False, message="Your anonymous session is invalid for voting. Please try again."), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        new_sigma = 0
        user_vote_status_after_action = 'none'
        poster_anon_id = None
        sigma_change = 0

        if item_type == 'post':
            cursor.execute('SELECT sigma, original_poster_anon_id FROM posts WHERE id = ?', (item_id,))
            item_row = cursor.fetchone()
            if item_row is None:
                return jsonify(success=False, message="Post not found."), 404
            current_sigma = item_row['sigma']
            poster_anon_id = item_row['original_poster_anon_id']

            cursor.execute('SELECT type FROM votes WHERE post_id = ? AND voter_anon_id = ?', (item_id, anon_id))
            current_vote_row = cursor.fetchone()

            if current_vote_row:
                old_vote_type = current_vote_row['type']
                if old_vote_type == vote_type:
                    cursor.execute('DELETE FROM votes WHERE post_id = ? AND voter_anon_id = ?', (item_id, anon_id))
                    sigma_change = -1 if vote_type == 'up' else 1
                    user_vote_status_after_action = 'none'
                else:
                    cursor.execute('UPDATE votes SET type = ? WHERE post_id = ? AND voter_anon_id = ?', (vote_type, item_id, anon_id))
                    sigma_change = 2 if vote_type == 'up' else -2
                    user_vote_status_after_action = vote_type
            else:
                cursor.execute('INSERT INTO votes (post_id, voter_anon_id, type) VALUES (?, ?, ?)', (item_id, anon_id, vote_type))
                sigma_change = 1 if vote_type == 'up' else -1
                user_vote_status_after_action = vote_type

            new_sigma = current_sigma + sigma_change
            cursor.execute('UPDATE posts SET sigma = ? WHERE id = ?', (new_sigma, item_id))

        elif item_type == 'comment':
            cursor.execute('SELECT sigma, commenter_anon_id FROM comments WHERE id = ?', (item_id,))
            item_row = cursor.fetchone()
            if item_row is None:
                return jsonify(success=False, message="Comment not found."), 404
            current_sigma = item_row['sigma']
            poster_anon_id = item_row['commenter_anon_id']

            cursor.execute('SELECT type FROM comment_votes WHERE comment_id = ? AND voter_anon_id = ?', (item_id, anon_id))
            current_vote_row = cursor.fetchone()

            if current_vote_row:
                old_vote_type = current_vote_row['type']
                if old_vote_type == vote_type:
                    cursor.execute('DELETE FROM comment_votes WHERE comment_id = ? AND voter_anon_id = ?', (item_id, anon_id))
                    sigma_change = -1 if vote_type == 'up' else 1
                    user_vote_status_after_action = 'none'
                else:
                    cursor.execute('UPDATE comment_votes SET type = ? WHERE comment_id = ? AND voter_anon_id = ?', (vote_type, item_id, anon_id))
                    sigma_change = 2 if vote_type == 'up' else -2
                    user_vote_status_after_action = vote_type
            else:
                cursor.execute('INSERT INTO comment_votes (comment_id, voter_anon_id, type) VALUES (?, ?, ?)', (item_id, anon_id, vote_type))
                sigma_change = 1 if vote_type == 'up' else -1
                user_vote_status_after_action = vote_type

            new_sigma = current_sigma + sigma_change
            cursor.execute('UPDATE comments SET sigma = ? WHERE id = ?', (new_sigma, item_id))

        cursor.execute('UPDATE users SET total_sigma = total_sigma + ? WHERE anon_id = ?', (sigma_change, poster_anon_id))
        conn.commit()

        cursor.execute("SELECT total_sigma FROM users WHERE anon_id = ?", (poster_anon_id,))
        updated_total_sigma_for_poster = cursor.fetchone()['total_sigma']

        socketio.emit('update_sigma', {'anon_id': poster_anon_id, 'new_sigma': updated_total_sigma_for_poster})

        if item_type == 'post':
            socketio.emit('update_post_sigma', {'post_id': item_id, 'new_sigma': new_sigma})
        elif item_type == 'comment':
            # Need to get post_id to emit to the correct room for comment updates on post detail page
            cursor.execute("SELECT post_id FROM comments WHERE id = ?", (item_id,))
            post_id_for_comment_update = cursor.fetchone()['post_id']
            socketio.emit('update_comment_sigma', {'comment_id': item_id, 'new_sigma': new_sigma, 'post_id': post_id_for_comment_update})

        return jsonify(success=True, new_score=new_sigma, user_vote_status=user_vote_status_after_action)

    except sqlite3.Error as se:
        if conn:
            conn.rollback()
        print(f"Database error during voting: {se}", file=sys.stderr)
        return jsonify(success=False, message=f"Database error: {se}"), 500
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"An unexpected error occurred during vote: {e}", file=sys.stderr)
        return jsonify({'success': False, 'message': f'An error occurred: {e}'}), 500
    finally:
        if conn:
            conn.close()

@app.route("/photos")
def photos():
    sort = request.args.get("sort", "hottest")
    view = request.args.get("view", "grid")
    search_query = request.args.get("q", "").strip()

    query = "SELECT p.id, p.username, p.content, p.image_filename, p.created, p.sigma, p.original_poster_anon_id, p.title FROM posts p WHERE p.image_filename IS NOT NULL AND p.image_filename != ''"
    filters = []
    params = []

    if search_query:
        filters.append("(p.username LIKE ? OR p.content LIKE ? OR p.title LIKE ?)")
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if filters:
        query += " AND " + " AND ".join(filters)

    if sort == "hottest":
        query += " ORDER BY p.sigma DESC"
    elif sort == "latest":
        query += " ORDER BY p.created DESC"
    else:
        query += " ORDER BY p.sigma DESC"

    posts_data = []
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        fetched_posts = cursor.fetchall()

        for post_row in fetched_posts:
            post_id = post_row['id']
            user_vote_type = None
            if g.anon_id != "0000":
                cursor.execute("SELECT type FROM votes WHERE post_id = ? AND voter_anon_id = ?", (post_id, g.anon_id))
                vote_row = cursor.fetchone()
                if vote_row:
                    user_vote_type = vote_row['type']

            cursor.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,))
            total_comments = cursor.fetchone()[0]

            # post: [id, username, content, image_filename, created, sigma, original_poster_anon_id, user_vote_status, total_comments, title]
            post_list = [
                post_row['id'],
                post_row['username'],
                post_row['content'],
                post_row['image_filename'],
                post_row['created'],
                post_row['sigma'],
                post_row['original_poster_anon_id'],
                user_vote_type,
                total_comments,
                post_row['title']
            ]
            posts_data.append(tuple(post_list))

    except Exception as e:
        flash(f"Error loading photos: {str(e)}", "danger")
        posts_data = []
    finally:
        conn.close()

    delete_post_form = DeletePostForm() # For delete buttons in photos view

    return render_template(
        "photos.html",
        posts=posts_data,
        anon_id=g.anon_id,
        join_date=g.join_date,
        sigma_score=g.sigma_score,
        threads_created=g.threads_created,
        comments_made=g.comments_made,
        total_likes_received_on_posts=g.total_likes_received_on_posts,
        sort=sort,
        view=view,
        search_query=search_query,
        delete_post_form=delete_post_form
    )

# app.py snippet for /text_discussions route
@app.route("/text")
def text_discussions():
    sort = request.args.get("sort", "best")
    view = request.args.get("view", "card")
    search_query = request.args.get("q", "").strip()

    query = "SELECT p.id, p.username, p.content, p.image_filename, p.created, p.sigma, p.original_poster_anon_id, p.title FROM posts p WHERE p.image_filename IS NULL OR p.image_filename = ''"
    filters = []
    params = []

    if search_query:
        filters.append("(p.username LIKE ? OR p.content LIKE ? OR p.title LIKE ?)")
        params.extend([f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'])

    if filters:
        query += " AND " + " AND ".join(filters)

    if sort == "hottest":
        query += " ORDER BY p.sigma DESC"
    elif sort == "best":
        query += " ORDER BY (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) DESC"
    else:
        query += " ORDER BY p.created DESC"

    posts_data = []
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        fetched_posts = cursor.fetchall()

        for post_row in fetched_posts:
            post_id = post_row['id']

            cursor.execute("SELECT id, commenter_anon_id, content, created, sigma FROM comments WHERE post_id = ? ORDER BY created DESC LIMIT 2", (post_id,))
            comments = cursor.fetchall()

            formatted_comments = []
            for comment_row in comments:
                # comment_list structure: [id, commenter_anon_id, content, sigma, user_comment_vote_type, created]
                comment_list = [
                    comment_row['id'],
                    comment_row['commenter_anon_id'],
                    comment_row['content'],
                    comment_row['sigma'],
                    None, # Placeholder for user_comment_vote_type, not fetched here for text view
                    comment_row['created'] # raw timestamp string
                ]
                formatted_comments.append(tuple(comment_list))

            cursor.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,))
            total_comments = cursor.fetchone()[0]

            user_vote_type = None
            if g.anon_id != "0000":
                cursor.execute("SELECT type FROM votes WHERE post_id = ? AND voter_anon_id = ?", (post_id, g.anon_id))
                vote_row = cursor.fetchone()
                if vote_row:
                    user_vote_type = vote_row['type']

            # post_list structure for /text_discussions:
            # Aligned with the provided Jinja2 snippet's current indices to minimize template changes
            # [0] id
            # [1] content (post body/description, now used for data-markdown-content for body)
            # [2] title (post title, now used for data-markdown-content for title)
            # [3] image_filename (will be None/empty for text posts)
            # [4] created (timestamp string)
            # [5] sigma (likes count)
            # [6] original_poster_anon_id (the numerical ID, e.g., '2782')
            # [7] formatted_comments (list of 2 recent comments)
            # [8] total_comments
            # [9] user_vote_type ('up', 'down', or None)
            post_list = [
                post_row['id'],
                post_row['content'],            # Corresponds to post[1] in your Jinja2 (for body)
                post_row['title'],              # Corresponds to post[2] in your Jinja2 (for title)
                post_row['image_filename'],     # Corresponds to post[3] in your Jinja2
                post_row['created'],            # Corresponds to post[4] in your Jinja2 (timestamp)
                post_row['sigma'],              # Corresponds to post[5] in your Jinja2
                post_row['original_poster_anon_id'], # Corresponds to post[6] in your Jinja2 (Anon ID)
                formatted_comments,             # Corresponds to post[7] in your Jinja2
                total_comments,                 # Corresponds to post[8] in your Jinja2
                user_vote_type                  # Corresponds to post[9] in your Jinja2
            ]
            posts_data.append(tuple(post_list))
    except Exception as e:
        flash(f"Error loading threads: {str(e)}", "danger")
        posts_data = []
    finally:
        conn.close()

    # Ensure DeletePostForm is instantiated and passed to the template
    # You might need to import DeletePostForm if not already done:
    # from your_forms_file import DeletePostForm # Example
    # from wtforms import StringField, TextAreaField, SubmitField # if DeletePostForm is not a separate class
    # For now, assuming it's correctly defined elsewhere or you have it in scope.
    # If not, you might need a simple FlaskForm if DeletePostForm is not fully set up.
    from flask_wtf import FlaskForm
    from wtforms import HiddenField
    class DeletePostForm(FlaskForm):
        post_id = HiddenField('post_id') # Example, adjust as per your actual form definition

    delete_post_form = DeletePostForm() # Instantiate the form

    return render_template(
        "text.html",
        posts=posts_data,
        anon_id=g.anon_id,
        join_date=g.join_date,
        sigma_score=g.sigma_score,
        threads_created=g.threads_created,
        comments_made=g.comments_made,
        total_likes_received_on_posts=g.total_likes_received_on_posts,
        sort=sort,
        view=view,
        search_query=search_query,
        delete_post_form=delete_post_form # Pass the form to the template
    )
            

@app.route("/upload_image", methods=["POST"])
def upload_image():
    anon_id = g.anon_id

    if 'image_file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('upload_form'))

    file = request.files['image_file']
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()

    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('upload_form'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"{anon_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        file_size = len(file.read()) # Read file size *before* saving
        file.seek(0) # Reset file pointer after reading size
        if file_size > app.config['MAX_CONTENT_LENGTH']:
            flash(f'File size exceeds limit of {app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024):.0f} MB.', 'warning')
            return redirect(url_for('upload_form'))

        file.save(file_path)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO posts (username, content, image_filename, created, sigma, original_poster_anon_id, title) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"Anon{anon_id}", description, unique_filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 0, anon_id, title)
            )
            conn.commit()
            flash('Image uploaded successfully!', 'success')
            return redirect(url_for('photos'))
        except sqlite3.Error as e:
            conn.rollback()
            flash(f"Database error: {e}", "danger")
            if os.path.exists(file_path):
                os.remove(file_path)
            return redirect(url_for('upload_form'))
        finally:
            conn.close()
    else:
        flash('Invalid file type. Allowed types are png, jpg, jpeg, gif, mp4, webm, ogg.', 'danger')
        return redirect(url_for('upload_form'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/create_thread_post", methods=['GET', 'POST'])
def create_thread_post():
    anon_id = g.anon_id

    form = PostForm() # Using PostForm for thread posts too
    if form.validate_on_submit():
        title = form.title.data
        content = form.content.data
        username = f"Anon{anon_id}"

        if not content and not title:
            flash("Thread post must contain a title or body content.", 'warning')
            return render_template('post.html', form=form)

        conn = get_db_connection()
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute('''
                INSERT INTO posts (username, content, title, image_filename, created, sigma, original_poster_anon_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (username, content, title, None, current_time, 0, anon_id))
            conn.commit()
            flash("Thread post created successfully!", 'success')
            return redirect(url_for('text_discussions'))
        except Exception as e:
            flash(f"Error creating thread post: {str(e)}", 'danger')
            return render_template('post.html', form=form)
        finally:
            conn.close()

    return render_template("post.html",
                           form=form,
                           anon_id=anon_id,
                           join_date=g.join_date,
                           sigma_score=g.sigma_score)

## SocketIO Event Handlers
@socketio.on('connect')
def handle_connect():
    anon_id = session.get("anon_id")
    if anon_id and anon_id != "0000":
        join_room(anon_id)
        print(f"Client {request.sid} joined room {anon_id}")
    else:
        print(f"Client {request.sid} connected without anon_id")

@socketio.on('disconnect')
def handle_disconnect():
    anon_id = session.get("anon_id")
    if anon_id and anon_id != "0000":
        leave_room(anon_id)
        print(f"Client {request.sid} left room {anon_id}")
    print("Client disconnected")

@app.route("/upload_form")
def upload_form():
    return render_template("upload_form.html",
                           anon_id=g.anon_id,
                           join_date=g.join_date,
                           sigma_score=g.sigma_score)

@app.route('/')
def index():
    upload_url = url_for('upload_form')
    add_comment_example_url = url_for('post_detail', post_id=1)
    return f"""
    <p>Go to <a href="{upload_url}">Upload Page</a></p>
    <p>Visit a post detail page to add comments (e.g., <a href="{add_comment_example_url}">Post 1 Detail</a>)</p>
    <p>Visit <a href="{url_for('feed')}">Feed</a></p>
    <p>Visit <a href="{url_for('photos')}">Photos</a></p>
    <p>Visit <a href="{url_for('text_discussions')}">Text Discussions</a></p>
    <p>Visit <a href="{url_for('create_post')}">Create Post (Unified)</a></p>
    <p>Visit <a href="{url_for('create_thread_post')}">Create Thread Post (Specific to Text)</a></p>
    """

# Run the app with SocketIO
if __name__ == '__main__':
    socketio.run(app, debug=True)