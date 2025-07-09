# Social Media Project: Demo Status & Vision 🚀

---

**Why There's No Live Demo Link (and Why It's Not a Simple Click):**

You might be looking for a live demo link, but unfortunately, one isn't **consistently available** for this project. This application is currently running in a **local development environment** 💻 on a personal computer. 
Making a local server (like the one running on `http://127.0.0.1:5000`) publicly accessible is **inherently complex** and absolutely **not a "one-click" operation** 🖱️. Your home network acts as a **private barrier**, and direct access from the internet is **blocked by firewalls** 🛡️ for security.

While temporary "**tunneling services**" (like ngrok or localtunnel) can bridge this gap, they require several things: the host computer must be **constantly on** 🔋, the Flask application needs to be **running without errors** ✅, and the tunneling software must be **actively forwarding traffic**.
Furthermore, this project uses an **SQLite database**, which is **volatile in a local setup** ⚠️; any data (posts, comments, user accounts) is **lost if the application restarts** or the computer sleeps.

For a **stable, always-on public link**, a proper deployment to a **cloud hosting platform** with a **persistent database** 🌐 is required, which is beyond the scope of this particular demo setup.

**However, to showcase the project's functionality, you can watch a comprehensive demo video here:**
https://screenapp.io/app/#/shared/chLA_iOdHI 🎬 This video offers a clear walkthrough of all features and user interactions.

---

## What is This Project? 💡

This project is a Flask-based social media application designed to demonstrate the **fundamental functionalities** found in modern social platforms. It serves as a **proof-of-concept** for real-time, interactive web applications.

Key features include:
* **Anonymous User Interaction:** 🎭 Users are assigned unique anonymous IDs, allowing for engagement without traditional login.
* **Content Posting:** ✍️ Users can create and share text-based posts (with potential for media uploads 🖼️).
* **Interactive Comments:** 💬 The ability to comment on existing posts, fostering discussion.
* **Voting System:** 👍👎 A dynamic "like" (upvote/downvote) system for both posts and comments, which influences a "**sigma score**" to indicate content popularity.
* **Real-time Communication:** ⚡ Leverages Flask-SocketIO to enable **instant updates**, such as live vote counts, across all connected clients.
* **Data Storage:** 🗄️ Utilizes an SQLite database to manage user profiles, posts, comments, and voting records.
* **Modern Frontend:** ✨ Built with Bootstrap for responsiveness and Font Awesome for icons, providing an intuitive user interface.


