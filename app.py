from flask import Flask, render_template, request, redirect
from flask import session, redirect, url_for
import mysql.connector

app = Flask(__name__)
app.secret_key = "school_system_secret_123"

def login_required():
    return 'user_id' in session

def is_admin():
    return session.get('role') == 'admin'

db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="root123",
    database="school_db"
)

cursor = db.cursor(buffered=True)

# 🔥 Cleanup orphaned teacher records on startup
def cleanup_orphaned_teachers():
    try:
        cursor.execute("""
            DELETE FROM teachers 
            WHERE user_id NOT IN (SELECT id FROM users WHERE role='teacher')
        """)
        db.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            print(f"✓ Cleaned up {deleted} orphaned teacher record(s)")
    except Exception as e:
        print(f"Cleanup error: {e}")

cleanup_orphaned_teachers()

@app.route('/')
def home():
    return "School System is Running!"

@app.route('/teachers')
def teachers():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied: Admin Only"

    # 🔥 Show ONLY synced teachers (teachers with valid user accounts)
    cursor.execute("""
        SELECT 
            t.id,
            u.id as user_id,
            u.username,
            t.name,
            t.email,
            t.department
        FROM teachers t
        INNER JOIN users u ON t.user_id = u.id
        WHERE u.role = 'teacher'
        ORDER BY t.name ASC
    """)
    data = cursor.fetchall()
    return render_template("teachers.html", teachers=data)

@app.route('/add-teacher', methods=['GET', 'POST'])
def add_teacher():
    if not login_required() or not is_admin():
        return redirect('/login')
        
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        department = request.form['department']
        username = request.form['username']
        password = request.form['password']

        # 1. Create user account FIRST (so we have the user_id)
        cursor.execute("""
            INSERT INTO users (username, password, role)
            VALUES (%s, %s, 'teacher')
        """, (username, password))
        db.commit()
        user_id = cursor.lastrowid

        # 2. Insert teacher with user_id linked immediately
        cursor.execute("""
            INSERT INTO teachers (name, email, department, user_id)
            VALUES (%s, %s, %s, %s)
        """, (name, email, department, user_id))
        db.commit()

        return redirect('/teachers')

    return render_template("add_teacher.html")

@app.route('/delete-teacher/<int:id>')
def delete_teacher(id):
    if not login_required():
        return redirect('/login')

    # Step 1: get user_id first
    cursor.execute("SELECT user_id FROM teachers WHERE id=%s", (id,))
    result = cursor.fetchone()
    if not result:
        return redirect('/teachers')
    user_id = result[0]

    # Step 2: delete relationships first
    cursor.execute("DELETE FROM teacher_subject WHERE teacher_id=%s", (id,))

    # Step 3: delete teacher record
    cursor.execute("DELETE FROM teachers WHERE id=%s", (id,))

    # Step 4: delete user account (cascade delete)
    if user_id:
        cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))

    db.commit()
    return redirect('/teachers')

@app.route('/edit-teacher/<int:id>', methods=['GET', 'POST'])
def edit_teacher(id):
    if not login_required():
        return redirect('/login')
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        department = request.form['department']

        sql = "UPDATE teachers SET name=%s, email=%s, department=%s WHERE id=%s"
        cursor.execute(sql, (name, email, department, id))
        db.commit()

        return redirect('/teachers')

    cursor.execute("SELECT * FROM teachers WHERE id=%s", (id,))
    teacher = cursor.fetchone()

    return render_template('edit_teacher.html', teacher=teacher)

@app.route('/students')
def students():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied: Admin Only"

    # Get filter parameter
    section_filter = request.args.get('section')

    if section_filter:
        cursor.execute("""
            SELECT students.id, students.name, students.grade_level,
                   sections.grade_level, sections.section_name
            FROM students
            JOIN sections ON students.section_id = sections.id
            WHERE students.section_id = %s
        """, (section_filter,))
    else:
        cursor.execute("""
            SELECT students.id, students.name, students.grade_level,
                   sections.grade_level, sections.section_name
            FROM students
            JOIN sections ON students.section_id = sections.id
        """)

    data = cursor.fetchall()

    # Load all sections for the filter dropdown
    cursor.execute("SELECT * FROM sections")
    sections = cursor.fetchall()

    return render_template("students.html", students=data, sections=sections)

@app.route('/add-student', methods=['GET', 'POST'])
def add_student():
    if not login_required():
        return redirect('/login')

    if request.method == 'POST':
        name = request.form['name']
        grade_level = request.form['grade_level']
        section_id = request.form['section_id']

        cursor.execute("""
            INSERT INTO students (name, grade_level, section_id)
            VALUES (%s, %s, %s)
        """, (name, grade_level, section_id))

        db.commit()
        return redirect('/students')

    # 🔥 LOAD SECTIONS
    cursor.execute("SELECT * FROM sections")
    sections = cursor.fetchall()

    return render_template("add_student.html", sections=sections)

@app.route('/delete-student/<int:id>')
def delete_student(id):
    if not login_required():
        return redirect('/login')
    
    # Step 1: Delete grades for this student
    cursor.execute("DELETE FROM grades WHERE student_id=%s", (id,))
    
    # Step 2: Delete enrollments for this student
    cursor.execute("DELETE FROM enrollments WHERE student_id=%s", (id,))
    
    # Step 3: Delete the student
    cursor.execute("DELETE FROM students WHERE id=%s", (id,))
    
    db.commit()
    return redirect('/students')

@app.route('/edit-student/<int:id>', methods=['GET', 'POST'])
def edit_student(id):
    if not login_required():
        return redirect('/login')
    
    if request.method == 'POST':
        name = request.form['name']
        grade_level = request.form['grade_level']
        section_id = request.form['section_id']

        sql = "UPDATE students SET name=%s, grade_level=%s, section_id=%s WHERE id=%s"
        cursor.execute(sql, (name, grade_level, section_id, id))
        db.commit()

        return redirect('/students')

    # Get student data
    cursor.execute("SELECT * FROM students WHERE id=%s", (id,))
    student = cursor.fetchone()

    # Get all sections for dropdown
    cursor.execute("SELECT * FROM sections")
    sections = cursor.fetchall()

    return render_template('edit_student.html', student=student, sections=sections)

@app.route('/subjects')
def subjects():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied: Admin Only"
    
    print("SESSION DATA:", session)

    cursor.execute("SELECT * FROM subjects")
    data = cursor.fetchall()
    return render_template("subjects.html", subjects=data)

@app.route('/add-subject', methods=['GET', 'POST'])
def add_subject():
    if not login_required():
        return redirect('/login')
    if request.method == 'POST':
        subject_name = request.form['subject_name']
        grade_level = request.form['grade_level']

        sql = "INSERT INTO subjects (subject_name, grade_level) VALUES (%s, %s)"
        cursor.execute(sql, (subject_name, grade_level))
        db.commit()

        return redirect('/subjects')

    return render_template("add_subject.html")

@app.route('/delete-subject/<int:id>')
def delete_subject(id):

    # 1. delete grades first
    cursor.execute("DELETE FROM grades WHERE subject_id = %s", (id,))

    # 2. delete enrollments (IMPORTANT if you added it)
    cursor.execute("DELETE FROM enrollments WHERE subject_id = %s", (id,))

    # 3. delete teacher assignments
    cursor.execute("DELETE FROM teacher_subject WHERE subject_id = %s", (id,))

    # 4. finally delete subject
    cursor.execute("DELETE FROM subjects WHERE id = %s", (id,))

    db.commit()

    return redirect('/subjects')

@app.route('/assign', methods=['GET', 'POST'])
def assign():
    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    if request.method == 'POST':
        teacher_id = request.form['teacher_id']
        subject_id = request.form['subject_id']

        cursor.execute("""
            INSERT INTO teacher_subject (teacher_id, subject_id)
            VALUES (%s, %s)
        """, (teacher_id, subject_id))

        db.commit()

        return redirect('/subjects?assigned=success')

    cursor.execute("SELECT * FROM teachers")
    teachers = cursor.fetchall()

    # 🔥 FILTERED — only these 8 subjects
    cursor.execute("""
    SELECT MIN(id) as id, subject_name 
                    FROM subjects 
                    GROUP BY subject_name 
                    ORDER BY subject_name
                    """)
    subjects = cursor.fetchall()

    return render_template("assign.html", teachers=teachers,subjects=subjects)

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor.execute(
            "SELECT * FROM users WHERE username=%s AND password=%s",
            (username, password)
        )

        user = cursor.fetchone()

        if user:
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[3]

            print("LOGIN SUCCESS ROLE:", user[3])

            if user[3] == 'admin':
                return redirect('/admin-dashboard')

            elif user[3] == 'teacher':
                return redirect('/teacher-dashboard')

            else:
                return redirect('/dashboard')

        print("LOGIN FAILED")
        return "Invalid login"

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if not login_required():
        return redirect('/login')

    role = session.get('role')

    if role == 'admin':
        return redirect('/admin-dashboard')
    elif role == 'teacher':
        return redirect('/teacher-dashboard')

    return redirect('/login')

@app.route('/teacher-dashboard')
def teacher_dashboard():

    if 'user_id' not in session:
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    user_id = session.get('user_id')

    # 🔥 STEP 1: get actual teacher_id
    cursor.execute("""
        SELECT id FROM teachers
        WHERE user_id = %s
    """, (user_id,))

    teacher = cursor.fetchone()

    if not teacher:
        return "Teacher profile not found"

    teacher_id = teacher[0]

    # 🔥 STEP 2: get subjects correctly
    cursor.execute("""
        SELECT subjects.id, subjects.subject_name, subjects.grade_level
        FROM teacher_subject
        JOIN subjects ON teacher_subject.subject_id = subjects.id
        WHERE teacher_subject.teacher_id = %s
    """, (teacher_id,))

    subjects = cursor.fetchall()

    return render_template("teacher_dashboard.html", subjects=subjects)

@app.route('/admin-dashboard')
def admin_dashboard():
    if 'user_id' not in session:
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"
    
    # Get counts
    cursor.execute("SELECT COUNT(*) FROM teachers")
    teacher_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM students")
    student_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM subjects")
    subject_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sections")
    section_count = cursor.fetchone()[0]

    # Get pending grade reviews count
    cursor.execute("""
        SELECT COUNT(DISTINCT subject_id) 
        FROM grades 
        WHERE status = 'submitted'
    """)
    pending_reviews = cursor.fetchone()[0]

    return render_template("admin_dashboard.html",
                           teacher_count=teacher_count,
                           student_count=student_count,
                           subject_count=subject_count,
                           section_count=section_count,
                           pending_reviews=pending_reviews)

@app.route('/input-grades/<int:subject_id>', methods=['GET', 'POST'])
def input_grades(subject_id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    # 🔥 get correct teacher_id
    cursor.execute("""
        SELECT id FROM teachers WHERE user_id = %s
    """, (session.get('user_id'),))

    teacher = cursor.fetchone()

    if not teacher:
        return "Teacher profile not found"

    teacher_id = teacher[0]

    # 🛑 check if already submitted
    cursor.execute("""
        SELECT 1 FROM grades
        WHERE teacher_id=%s AND subject_id=%s AND status='submitted'
    """, (teacher_id, subject_id))

    if cursor.fetchone():
        return "Grades already submitted. Editing is locked."

    # 🔥 get students via section-subject system
    cursor.execute("""
        SELECT DISTINCT students.id, students.name
        FROM students
        JOIN sections ON students.section_id = sections.id
        JOIN section_subjects ON sections.id = section_subjects.section_id
        WHERE section_subjects.subject_id = %s
    """, (subject_id,))

    students = cursor.fetchall()

    if request.method == 'POST':

        quarter = request.form.get('quarter')

        if not quarter:
            return "Quarter is required"

        # 🔥 delete old drafts FIRST (safe place)
        cursor.execute("""
            DELETE FROM grades
            WHERE teacher_id=%s AND subject_id=%s AND status='draft'
        """, (teacher_id, subject_id))

        # 🔥 insert grades safely
        for student in students:
            student_id = student[0]
            grade = request.form.get(f'grade_{student_id}')

            # skip empty inputs
            if grade is None or grade.strip() == "":
                continue

            cursor.execute("""
                INSERT INTO grades (student_id, subject_id, teacher_id, grade, quarter, status)
                VALUES (%s, %s, %s, %s, %s, 'draft')
            """, (student_id, subject_id, teacher_id, grade, quarter))

        db.commit()
        return redirect('/teacher-dashboard')

    return render_template(
        "input_grades.html",
        students=students,
        subject_id=subject_id
    )
@app.route('/view-grades')
def view_grades():
    if not login_required():
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    teacher_id = session.get('user_id')

    cursor.execute("""
        SELECT students.name, subjects.subject_name, grades.grade, grades.quarter
        FROM grades
        JOIN students ON grades.student_id = students.id
        JOIN subjects ON grades.subject_id = subjects.id
        WHERE grades.teacher_id = %s
    """, (teacher_id,))

    data = cursor.fetchall()

    return render_template("view_grades.html", grades=data)

@app.route('/all-grades')
def all_grades():

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    cursor.execute("""
        SELECT 
            grades.id,
            students.name,
            subjects.subject_name,
            teachers.name,
            grades.grade,
            grades.quarter,
            grades.status
        FROM grades
        JOIN students ON grades.student_id = students.id
        JOIN subjects ON grades.subject_id = subjects.id
        JOIN teachers ON grades.teacher_id = teachers.id
        ORDER BY grades.status ASC
    """)

    data = cursor.fetchall()

    return render_template("all_grades.html", grades=data)

@app.route('/submit-grades/<int:subject_id>')
def submit_grades(subject_id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    # get real teacher_id
    cursor.execute("""
        SELECT id FROM teachers WHERE user_id = %s
    """, (session.get('user_id'),))

    teacher = cursor.fetchone()

    if not teacher:
        return "Teacher not found"

    teacher_id = teacher[0]

    # 🔥 DEBUG (optional but helpful)
    cursor.execute("""
        SELECT COUNT(*) FROM grades
        WHERE teacher_id=%s AND subject_id=%s
    """, (teacher_id, subject_id))

    print("GRADES FOUND:", cursor.fetchone())

    # update status
    cursor.execute("""
        UPDATE grades
        SET status='submitted'
        WHERE teacher_id=%s AND subject_id=%s AND status='draft'
    """, (teacher_id, subject_id))

    db.commit()

    return redirect('/teacher-dashboard')

@app.route('/approve-grades/<int:subject_id>')
def approve_grades(subject_id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    cursor.execute("""
        UPDATE grades
        SET status='approved'
        WHERE subject_id=%s AND status='submitted'
    """, (subject_id,))

    db.commit()

    return redirect('/admin-grade-review')

@app.route('/approve-grade/<int:grade_id>')
def approve_grade(grade_id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    cursor.execute("""
        UPDATE grades
        SET status='approved'
        WHERE id=%s
    """, (grade_id,))

    db.commit()

    return redirect('/all-grades')

from flask import session, redirect

@app.route('/assign-students', methods=['GET', 'POST'])
def assign_students():
    if 'user_id' not in session:
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    if request.method == 'POST':
        student_id = request.form['student_id']
        subject_id = request.form['subject_id']
        section = request.form['section']

        cursor.execute("""
            INSERT INTO enrollments (student_id, subject_id, section)
            VALUES (%s, %s, %s)
        """, (student_id, subject_id, section))

        db.commit()
        return "Student Assigned!"

    cursor.execute("SELECT * FROM students")
    students = cursor.fetchall()

    cursor.execute("SELECT * FROM subjects")
    subjects = cursor.fetchall()

    return render_template("assign_students.html", students=students, subjects=subjects)

@app.route('/sections', methods=['GET'])
def sections():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied"

    cursor.execute("SELECT * FROM sections")
    data = cursor.fetchall()

    return render_template("sections.html", sections=data)

@app.route('/add-section', methods=['GET', 'POST'])
def add_section():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied"

    if request.method == 'POST':
        grade_level = request.form['grade_level']
        section_name = request.form['section_name']

        cursor.execute("""
            INSERT INTO sections (grade_level, section_name)
            VALUES (%s, %s)
        """, (grade_level, section_name))

        db.commit()
        return redirect('/sections')

    return render_template("add_section.html")

@app.route('/edit-section/<int:id>', methods=['GET', 'POST'])
def edit_section(id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    if request.method == 'POST':
        grade_level = request.form['grade_level']
        section_name = request.form['section_name']

        cursor.execute("""
            UPDATE sections
            SET grade_level=%s, section_name=%s
            WHERE id=%s
        """, (grade_level, section_name, id))

        db.commit()
        return redirect('/sections')

    cursor.execute("SELECT * FROM sections WHERE id=%s", (id,))
    section = cursor.fetchone()

    return render_template("edit_section.html", section=section)

@app.route('/delete-section/<int:id>')
def delete_section(id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    # 🔥 remove students from section
    cursor.execute("""
        UPDATE students SET section_id = NULL WHERE section_id = %s
    """, (id,))

    # 🔥 remove subject assignments
    cursor.execute("""
        DELETE FROM section_subjects WHERE section_id = %s
    """, (id,))

    # 🔥 delete section
    cursor.execute("""
        DELETE FROM sections WHERE id = %s
    """, (id,))

    db.commit()

    return redirect('/sections')

@app.route('/assign-section-subject', methods=['GET', 'POST'])
def assign_section_subject():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied"

    if request.method == 'POST':
        section_id = request.form['section_id']
        subject_id = request.form['subject_id']

        cursor.execute("""
            INSERT INTO section_subjects (section_id, subject_id)
            VALUES (%s, %s)
        """, (section_id, subject_id))

        db.commit()
        return redirect('/assign-section-subject')

    # load sections
    cursor.execute("SELECT * FROM sections")
    sections = cursor.fetchall()

    # 🔥 Get one ID per subject name (no duplicates)
    cursor.execute("""
        SELECT MIN(id) as id, subject_name 
        FROM subjects 
        GROUP BY subject_name 
        ORDER BY subject_name
    """)
    subjects = cursor.fetchall()

    return render_template(
        "assign_section_subject.html",
        sections=sections,
        subjects=subjects
    )

@app.route('/admin-grade-review')
def admin_grade_review():
    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    # optional filters
    subject_filter = request.args.get('subject')
    status_filter = request.args.get('status')

    query = """
        SELECT 
            subjects.id,
            subjects.subject_name,
            teachers.name,
            COUNT(grades.id) as total_records,
            grades.status
        FROM grades
        JOIN subjects ON grades.subject_id = subjects.id
        JOIN teachers ON grades.teacher_id = teachers.id
    """

    conditions = []
    params = []

    if subject_filter:
        conditions.append("subjects.id = %s")
        params.append(subject_filter)

    if status_filter:
        conditions.append("grades.status = %s")
        params.append(status_filter)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " GROUP BY subjects.id, teachers.name, grades.status"

    cursor.execute(query, params)
    data = cursor.fetchall()

    # load dropdown filters
    cursor.execute("SELECT * FROM subjects")
    subjects = cursor.fetchall()

    return render_template(
        "admin_grade_review.html",
        data=data,
        subjects=subjects
    )

@app.route('/view-submitted-grades')
def view_submitted_grades():

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    # get teacher_id from session user_id
    cursor.execute("""
        SELECT id FROM teachers WHERE user_id = %s
    """, (session.get('user_id'),))

    teacher = cursor.fetchone()

    if not teacher:
        return "Teacher not found"

    teacher_id = teacher[0]

    cursor.execute("""
        SELECT 
            students.name,
            subjects.subject_name,
            grades.grade,
            grades.quarter,
            grades.status
        FROM grades
        JOIN students ON grades.student_id = students.id
        JOIN subjects ON grades.subject_id = subjects.id
        WHERE grades.teacher_id = %s
        ORDER BY grades.quarter DESC
    """, (teacher_id,))

    data = cursor.fetchall()

    return render_template("view_submitted_grades.html", grades=data)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

if __name__ == "__main__":
    app.run(debug=True)