from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import os
from werkzeug.utils import secure_filename
from datetime import datetime

#App Setup

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'


UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD-FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

import os

UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # This will make both folders


def init_db():
    #Connect to datebase file
    connection = sqlite3.connect('database.db')
    #Create a cursor to run SQL commands
    cursor = connection.cursor()
    #WRITE A SQL COMMAND WITH 5 COLUMNS IT CREATES A TABLE NAMED POSTS

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            content TEXT,
            image TEXT,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    connection.commit()
    connection.close()

# HOME PAGE
def home():
    """Home page showing all posts"""
    try:
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM posts ORDER BY created DESC")
        posts = cursor.fetchall
        conn.close()
    except  Exception as e:
        flash(f"Error loading posts: {str(e)}")
        posts = []    
    return render_template('feed.html', posts=posts)

@app.route('/post', methods=['GET', 'POST'])
def post():
    if request.method == 'POST':
        username = request.form.get('username', '').strip() or 'anon'
        content = request.form.get('content', '').strip()
        image_file = request.files.get('image')

        image_filename = None

        if image_file and image_file.filename:
            if allowed_file(image_file.filename): # type: ignore
                filename = secure_filename(image_file.filename)
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                image_file.save(image_path)
                image_filename= filename
            else:
                flash('Image type not allowed' 'Use PNG, JPG, JPEG, GIF.')
                return redirect(request.url)
            
        if not content and not image_filename:
            flash("POSTS MUST HAVE TEXT OR AN IMAGE")
            return redirect(request.irl)
        
        try:
            conn= sqlite3.connect('database.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO posts (username, content, image)
                VALUES (?,?,?)
            ''', (username, content, image_filename))
            conn.commit()
            conn.close()
            flash('Post uploaded successfully')
            return redirect(url_for('home'))
        except Exception as e:
            flash(f'Failed to post: {str(e)}')
            return redirect(request.url)
        
    return render_template('post.html')
@app.route('/')
def feed():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM posts ORDER BY id DESC")
    posts = c.fetchall()
    conn.close()
    return render_template('feed.html', posts=posts) 

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

        



