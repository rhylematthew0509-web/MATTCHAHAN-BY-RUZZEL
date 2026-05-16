import os

from flask import Flask, render_template, request, redirect
from flask import session, redirect, url_for
import mysql.connector

app = Flask(__name__)
app.secret_key = "school_system_secret_123"

def login_required():
    return 'user_id' in session

def is_admin():
    return session.get('role') == 'admin'

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'school_db')
}

try:
    db = mysql.connector.connect(**DB_CONFIG)
    cursor = db.cursor(buffered=True)
except mysql.connector.Error as err:
    print("Database connection failed:", err)
    print("Expected DB settings:", {k: v for k, v in DB_CONFIG.items() if k != 'password'})
    raise

def get_next_id(table_name):
    cursor.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table_name}")
    return cursor.fetchone()[0]

# 🔥 Normalize existing zero-ID rows for broken tables
def normalize_zero_ids(table_name, key_columns):
    columns = ', '.join(key_columns)
    cursor.execute(f"SELECT {columns} FROM {table_name} WHERE id=0")
    rows = cursor.fetchall()
    if not rows:
        return

    cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")
    next_id = cursor.fetchone()[0]

    for row in rows:
        next_id += 1
        where_clause = ' AND '.join(f"{col}=%s" for col in key_columns)
        cursor.execute(
            f"UPDATE {table_name} SET id=%s WHERE id=0 AND {where_clause} LIMIT 1",
            (next_id, *row)
        )
    db.commit()

normalize_zero_ids('users', ['username', 'password', 'role'])
normalize_zero_ids('teachers', ['name', 'email', 'department', 'user_id'])

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

# 🔥 Remove duplicate teachers (keep first, delete duplicates)
def cleanup_duplicate_teachers():
    try:
        cursor.execute("""
            DELETE FROM teachers 
            WHERE id NOT IN (
                SELECT MIN(id) FROM teachers GROUP BY user_id
            )
        """)
        db.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            print(f"✓ Cleaned up {deleted} duplicate teacher record(s)")
    except Exception as e:
        print(f"Duplicate cleanup error: {e}")

cleanup_orphaned_teachers()
cleanup_duplicate_teachers()

# Ensure teacher_subject has section_id column
def ensure_teacher_subject_section_column():
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.columns 
            WHERE table_name = 'teacher_subject' AND column_name = 'section_id'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE teacher_subject ADD COLUMN section_id INT DEFAULT NULL")
            db.commit()
            print("Added section_id column to teacher_subject")
    except Exception as e:
        print(f"Column check error: {e}")

ensure_teacher_subject_section_column()

@app.route('/')
def home():
    return "School System is Running!"

@app.route('/teachers')
def teachers():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied: Admin Only"

    # Get teachers with their assigned subjects and grade levels
    # A teacher can have multiple subject assignments
    cursor.execute("""
        SELECT 
            t.id,
            t.name,
            t.email,
            s.subject_name,
            s.grade_level,
            sec.section_name
        FROM teachers t
        INNER JOIN users u ON t.user_id = u.id
        LEFT JOIN teacher_subject ts ON t.id = ts.teacher_id
        LEFT JOIN subjects s ON ts.subject_id = s.id
        LEFT JOIN sections sec ON ts.section_id = sec.id
        WHERE u.role = 'teacher'
        ORDER BY s.grade_level ASC, t.name ASC
    """)
    rows = cursor.fetchall()

    # Convert to list of dicts for easier template access
    teachers = []
    for row in rows:
        teachers.append({
            'id': row[0],
            'name': row[1],
            'email': row[2],
            'subject_name': row[3] or 'Unassigned',
            'grade_level': row[4] or 0,
            'section_name': row[5] or 'All Sections'
        })

    return render_template("teachers.html", teachers=teachers)

@app.route('/add-teacher', methods=['GET', 'POST'])
def add_teacher():
    if not login_required() or not is_admin():
        return redirect('/login')

    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        username = email

        # Use email as the teacher login identifier
        cursor.execute("SELECT COUNT(*) FROM users WHERE username=%s", (username,))
        if cursor.fetchone()[0] > 0:
            return "A teacher with this email/login already exists."

        cursor.execute("SELECT COUNT(*) FROM teachers WHERE email=%s", (email,))
        if cursor.fetchone()[0] > 0:
            return "A teacher with this email already exists."

        # 1. Create user account FIRST (so we have the user_id)
        user_id = get_next_id('users')
        cursor.execute("""
            INSERT INTO users (id, username, password, role)
            VALUES (%s, %s, %s, 'teacher')
        """, (user_id, username, password))
        db.commit()

        # 2. Insert teacher with user_id linked immediately
        teacher_id = get_next_id('teachers')
        cursor.execute("""
            INSERT INTO teachers (id, name, email, user_id)
            VALUES (%s, %s, %s, %s)
        """, (teacher_id, name, email, user_id))
        db.commit()

        # NOTE: Subject assignment is done separately via /assign page

        return redirect('/teachers')

    return render_template("add_teacher.html")

@app.route('/delete-teacher/<int:id>')
def delete_teacher(id):
    if not login_required():
        return redirect('/login')

    try:
        cursor.execute("SELECT user_id FROM teachers WHERE id=%s", (id,))
        result = cursor.fetchone()
        if not result:
            return redirect('/teachers')
        user_id = result[0]

        cursor.execute("DELETE FROM teacher_subject WHERE teacher_id=%s", (id,))
        cursor.execute("DELETE FROM teachers WHERE id=%s", (id,))

        if user_id:
            cursor.execute("SELECT COUNT(*) FROM teachers WHERE user_id=%s", (user_id,))
            remaining = cursor.fetchone()[0]
            if remaining == 0:
                cursor.execute("DELETE FROM users WHERE id=%s LIMIT 1", (user_id,))

        db.commit()
    except Exception as e:
        print(f"Error deleting teacher: {e}")
        db.rollback()

    return redirect('/teachers')

@app.route('/edit-teacher/<int:id>', methods=['GET', 'POST'])
def edit_teacher(id):
    if not login_required():
        return redirect('/login')
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        subject_id = request.form['subject']

        cursor.execute("SELECT subject_name FROM subjects WHERE id=%s", (subject_id,))
        subject_row = cursor.fetchone()
        if not subject_row:
            return "Selected subject not found."
        department = subject_row[0]

        sql = "UPDATE teachers SET name=%s, email=%s, department=%s WHERE id=%s"
        cursor.execute(sql, (name, email, department, id))

        cursor.execute("SELECT id FROM teacher_subject WHERE teacher_id=%s ORDER BY id LIMIT 1", (id,))
        existing_assignment = cursor.fetchone()
        if existing_assignment:
            cursor.execute(
                "UPDATE teacher_subject SET subject_id=%s WHERE id=%s",
                (subject_id, existing_assignment[0])
            )
        else:
            cursor.execute(
                "INSERT INTO teacher_subject (teacher_id, subject_id) VALUES (%s, %s)",
                (id, subject_id)
            )

        db.commit()

        return redirect('/teachers')

    cursor.execute("SELECT * FROM teachers WHERE id=%s", (id,))
    teacher = cursor.fetchone()

    # Load subjects for dropdown
    cursor.execute("SELECT id, subject_name FROM subjects ORDER BY subject_name")
    subjects = cursor.fetchall()

    return render_template('edit_teacher.html', teacher=teacher, subjects=subjects)

@app.route('/students')
def students():
    if not login_required():
        return redirect('/login')

    if not is_admin():
        return "Access Denied: Admin Only"

    # Get filter parameters
    section_filter = request.args.get('section')
    grade_filter = request.args.get('grade_level')

    query = """
        SELECT students.id, students.name, students.grade_level,
               sections.grade_level, sections.section_name
        FROM students
        JOIN sections ON students.section_id = sections.id
        WHERE 1=1
    """
    params = []

    if grade_filter:
        query += " AND students.grade_level = %s"
        params.append(grade_filter)

    if section_filter:
        query += " AND students.section_id = %s"
        params.append(section_filter)

    query += " ORDER BY students.grade_level ASC, students.name ASC"

    cursor.execute(query, tuple(params))
    data = cursor.fetchall()

    # Load all sections for the filter dropdown
    cursor.execute("SELECT * FROM sections ORDER BY grade_level ASC, section_name ASC")
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

    # Get subjects with their assigned teacher(s)
    cursor.execute("""
        SELECT 
            s.id,
            s.subject_name,
            s.grade_level,
            GROUP_CONCAT(DISTINCT t.name ORDER BY t.name SEPARATOR ', ') as teachers
        FROM subjects s
        LEFT JOIN teacher_subject ts ON s.id = ts.subject_id
        LEFT JOIN teachers t ON ts.teacher_id = t.id
        GROUP BY s.id, s.subject_name, s.grade_level
        ORDER BY s.grade_level ASC, s.subject_name ASC
    """)
    data = cursor.fetchall()
    print(f"DEBUG SUBJECTS: Found {len(data)} subjects")
    for row in data:
        print(f"  {row}")
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

@app.route('/edit-subject/<int:id>', methods=['GET', 'POST'])
def edit_subject(id):
    if not login_required():
        return redirect('/login')
    if session.get('role') != 'admin':
        return "Access Denied"

    if request.method == 'POST':
        subject_name = request.form['subject_name']
        grade_level = request.form['grade_level']

        cursor.execute("""
            UPDATE subjects SET subject_name=%s, grade_level=%s WHERE id=%s
        """, (subject_name, grade_level, id))
        db.commit()
        return redirect('/subjects')

    cursor.execute("SELECT * FROM subjects WHERE id=%s", (id,))
    subject = cursor.fetchone()

    return render_template('edit_subject.html', subject=subject)

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
        section_id = request.form.get('section_id') or None
        
        print(f"DEBUG ASSIGN: teacher_id={teacher_id}, subject_id={subject_id}, section_id={section_id}")

        # Check if this exact assignment already exists
        cursor.execute("""
            SELECT id FROM teacher_subject 
            WHERE teacher_id = %s AND subject_id = %s AND section_id <=> %s
        """, (teacher_id, subject_id, section_id))

        if cursor.fetchone():
            return "This teacher is already assigned to this subject/section. <a href='/assign'>Go back</a>"

        cursor.execute("""
            INSERT INTO teacher_subject (teacher_id, subject_id, section_id)
            VALUES (%s, %s, %s)
        """, (teacher_id, subject_id, section_id))

        db.commit()

        return redirect('/assign?assigned=success')

    # Only show teachers that have valid user accounts (not orphaned)
    cursor.execute("""
        SELECT t.id, t.name
        FROM teachers t
        INNER JOIN users u ON t.user_id = u.id
        WHERE u.role = 'teacher'
        ORDER BY t.name ASC
    """)
    teachers = cursor.fetchall()

    cursor.execute("SELECT id, subject_name, grade_level FROM subjects ORDER BY subject_name")
    subjects = cursor.fetchall()

    cursor.execute("SELECT id, grade_level, section_name FROM sections ORDER BY grade_level, section_name")
    sections = cursor.fetchall()

    # Get grades for the grade-level tab
    cursor.execute("""
        SELECT DISTINCT grade_level
        FROM (
            SELECT grade_level FROM sections
            UNION
            SELECT grade_level FROM subjects
        ) AS grades
        WHERE grade_level IS NOT NULL AND grade_level <> ''
        ORDER BY grade_level
    """)
    grades = [row[0] for row in cursor.fetchall()]

    return render_template("assign.html", 
                         teachers=teachers, 
                         subjects=subjects, 
                         sections=sections,
                         grades=grades,
                         assigned=request.args.get('assigned'),
                         updated=request.args.get('updated'))

@app.route('/assign-subject-grade-level', methods=['GET', 'POST'])
def assign_subject_grade_level():
    if not login_required():
        return redirect('/login')

    if session.get('role') != 'admin':
        return "Access Denied"

    if request.method == 'POST':
        subject_id = request.form['subject_id']
        grade_level = request.form['grade_level']

        cursor.execute(
            "UPDATE subjects SET grade_level=%s WHERE id=%s",
            (grade_level, subject_id)
        )
        db.commit()

        return redirect('/assign?tab=grade&updated=1')

    # Redirect GET requests to the merged page
    return redirect('/assign?tab=grade')

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

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

    # Get teacher_id
    cursor.execute("SELECT id FROM teachers WHERE user_id = %s", (user_id,))
    teacher = cursor.fetchone()

    if not teacher:
        return "Teacher profile not found"

    teacher_id = teacher[0]

    # Get assignments with subject_id explicitly
    cursor.execute("""
        SELECT 
            ts.id,
            s.subject_name,
            s.grade_level,
            CASE WHEN ts.section_id IS NULL THEN 'All Sections' ELSE sec.section_name END as section_name,
            ts.subject_id,
            ts.section_id
        FROM teacher_subject ts
        JOIN subjects s ON ts.subject_id = s.id
        LEFT JOIN sections sec ON ts.section_id = sec.id
        WHERE ts.teacher_id = %s
    """, (teacher_id,))

    rows = cursor.fetchall()

    # Build assignments with status
    assignments = []
    for row in rows:
        ts_id = row[0]
        subject_name = row[1]
        grade_level = row[2]
        section_name = row[3]
        subject_id = row[4]
        section_id = row[5]

        # Debug print (check your console)
        print(f"DEBUG: ts_id={ts_id}, subject_id={subject_id}, subject_name={subject_name}")

        # Check for submitted grades first
        cursor.execute("""
            SELECT 1 FROM grades 
            WHERE teacher_id = %s AND subject_id = %s AND status = 'submitted'
            LIMIT 1
        """, (teacher_id, subject_id))
        
        has_submitted = cursor.fetchone()
        print(f"DEBUG: has_submitted={has_submitted}")

        if has_submitted:
            status = 'submitted'
        else:
            # Check for draft grades
            cursor.execute("""
                SELECT 1 FROM grades 
                WHERE teacher_id = %s AND subject_id = %s AND status = 'draft'
                LIMIT 1
            """, (teacher_id, subject_id))
            
            has_draft = cursor.fetchone()
            print(f"DEBUG: has_draft={has_draft}")
            
            if has_draft:
                status = 'draft'
            else:
                status = 'none'

        print(f"DEBUG: final status={status}")
        assignments.append((ts_id, subject_name, grade_level, section_name, status))

    print(f"DEBUG: assignments={assignments}")
    return render_template("teacher_dashboard.html", assignments=assignments)



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

@app.route('/input-grades/<int:assignment_id>', methods=['GET', 'POST'])
def input_grades(assignment_id):

    if not login_required():
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    # Get teacher_id
    cursor.execute("SELECT id FROM teachers WHERE user_id = %s", (session.get('user_id'),))
    teacher = cursor.fetchone()

    if not teacher:
        return "Teacher profile not found"

    teacher_id = teacher[0]

    # Look up subject_id and section_id from this assignment
    cursor.execute("""
        SELECT subject_id, section_id 
        FROM teacher_subject 
        WHERE id = %s AND teacher_id = %s
    """, (assignment_id, teacher_id))
    
    assignment = cursor.fetchone()
    
    if not assignment:
        return "Assignment not found or access denied"
    
    subject_id = assignment[0]
    section_id = assignment[1]

    # Check if already submitted
    cursor.execute("""
        SELECT 1 FROM grades
        WHERE teacher_id=%s AND subject_id=%s AND status='submitted'
    """, (teacher_id, subject_id))

    if cursor.fetchone():
        return "Grades already submitted. Editing is locked."

    # Get students from the SPECIFIC section, or all students in the subject's grade level
    if section_id is not None:
        cursor.execute("""
            SELECT students.id, students.name
            FROM students
            WHERE students.section_id = %s
            ORDER BY students.name
        """, (section_id,))
    else:
        cursor.execute("SELECT grade_level FROM subjects WHERE id = %s", (subject_id,))
        subject_row = cursor.fetchone()
        if not subject_row:
            return "Subject not found"

        grade_level = subject_row[0]
        cursor.execute("""
            SELECT s.id, s.name
            FROM students s
            JOIN sections sec ON s.section_id = sec.id
            WHERE sec.grade_level = %s
            ORDER BY s.name
        """, (grade_level,))

    students = cursor.fetchall()

    if request.method == 'POST':

        quarter = request.form.get('quarter')

        if not quarter:
            return "Quarter is required"

        # Delete old drafts
        cursor.execute("""
            DELETE FROM grades
            WHERE teacher_id=%s AND subject_id=%s AND status='draft'
        """, (teacher_id, subject_id))

        # Insert grades
        for student in students:
            student_id = student[0]
            grade = request.form.get(f'grade_{student_id}')

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
        subject_id=subject_id,
        section_id=section_id
    )

@app.route('/view-grades')
def view_grades():
    if not login_required():
        return redirect('/login')

    if session.get('role') != 'teacher':
        return "Access Denied"

    cursor.execute("SELECT id FROM teachers WHERE user_id = %s", (session.get('user_id'),))
    teacher = cursor.fetchone()
    if not teacher:
        return "Teacher profile not found"
    teacher_id = teacher[0]

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

@app.route('/submit-grades/<int:assignment_id>')
def submit_grades(assignment_id):

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

    # Look up the real subject_id from the assignment
    cursor.execute("""
        SELECT subject_id FROM teacher_subject
        WHERE id = %s AND teacher_id = %s
    """, (assignment_id, teacher_id))
    
    assignment = cursor.fetchone()
    
    if not assignment:
        return "Assignment not found"
    
    subject_id = assignment[0]

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

@app.route('/api/section-students/<int:section_id>')
def api_section_students(section_id):
    if not login_required():
        return {"error": "Not logged in"}, 401
    if not is_admin():
        return {"error": "Access Denied"}, 403
    cursor.execute("SELECT name FROM students WHERE section_id = %s ORDER BY name ASC", (section_id,))
    students = [row[0] for row in cursor.fetchall()]
    return {"students": students}

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

    # 🔥 delete section
    cursor.execute("""
        DELETE FROM sections WHERE id = %s
    """, (id,))

    db.commit()

    return redirect('/sections')

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

@app.route('/assign-teacher-section', methods=['GET', 'POST'])
def assign_teacher_section():
    if 'user_id' not in session or session.get('role') != 'admin':
        return "Access Denied"

    if request.method == 'POST':
        teacher_id = request.form['teacher_id']
        subject_id = request.form['subject_id']
        section_id = request.form['section_id']

        cursor.execute("""
            INSERT INTO teacher_subject (teacher_id, subject_id, section_id)
            VALUES (%s, %s, %s)
        """, (teacher_id, subject_id, section_id))
        
        db.commit()  # or conn.commit() depending on your setup

        return redirect('/admin-dashboard')

    # GET: fetch dropdown data
    # Only show teachers that have valid user accounts (not orphaned)
    cursor.execute("""
        SELECT t.id, t.name
        FROM teachers t
        INNER JOIN users u ON t.user_id = u.id
        WHERE u.role = 'teacher'
        ORDER BY t.name ASC
    """)
    teachers = cursor.fetchall()

    cursor.execute("SELECT id, subject_name, grade_level FROM subjects ORDER BY subject_name")
    subjects = cursor.fetchall()

    cursor.execute("SELECT id, grade_level, section_name FROM sections ORDER BY grade_level, section_name")
    sections = cursor.fetchall()

    return render_template("assign_teacher_section.html", 
                         teachers=teachers, 
                         subjects=subjects, 
                         sections=sections)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/debug/assignments')
def debug_assignments():
    if not login_required():
        return {"error": "Not logged in"}, 401
    cursor.execute("SELECT * FROM teacher_subject LIMIT 20")
    rows = cursor.fetchall()
    cursor.execute("SELECT * FROM subjects LIMIT 10")
    subjects = cursor.fetchall()
    cursor.execute("SELECT id, name FROM teachers LIMIT 10")
    teachers = cursor.fetchall()
    return {
        "teacher_subject": [dict(zip(['id','teacher_id','subject_id','section_id'], row)) for row in rows],
        "subjects": [dict(zip(['id','subject_name','grade_level'], row)) for row in subjects],
        "teachers": [dict(zip(['id','name'], row)) for row in teachers]
    }

if __name__ == "__main__":
    app.run(debug=True)