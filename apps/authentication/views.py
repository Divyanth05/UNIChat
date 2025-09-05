# apps/authentication/views.py
"""
Smart Authentication Views
Step 1: Email check functionality
"""

from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.contrib.auth import authenticate
from .models import Student, User ,University
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework.permissions import IsAdminUser
import csv
import io
import re
from apps.chat.cache_utils import cache_user_auth, invalidate_user_auth_cache, refresh_user_auth_cache
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async


#USER AUTHENTICATION END POINTS
@api_view(['POST'])
@permission_classes([AllowAny])
async def check_email(request):
    """
    Smart Authentication Step 1: Check if email exists and determine next action
    
    How it works:
    1. Student enters email in frontend
    2. API checks if email exists in Student table
    3. If student exists, check if User account exists
    4. Return appropriate next step
    
    API Call:
    POST /api/v1/auth/check-email/
    {
        "email": "rjoshi@abc.edu"
    }
    
    Possible Responses:
    1. needs_password_setup - Student exists, no User account (first time)
    2. needs_login - Student exists, User account exists (returning user)
    3. invalid_email - Email not found in system
    """
    
    # Get email from request data
    email = request.data.get('email', '').lower().strip()
    
    # Basic validation
    if not email:
        return Response({
            'error': 'Email is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    if '@' not in email:
        return Response({
            'error': 'Invalid email format'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Step 1: Check if student exists with this email
        student = await Student.objects.select_related('university').get(email=email) 
        #how university name  is fetched ny using joins by django . it similar to join in sql. equilavent to student= students.objects.get(email=email) and university_name=student.university.name
        #Get the student AND their university data in ONE database query using a JOIN, so I don't have to make a second query later.
        print(f"✅ Student found: {student.unique_id} - {student.first_name} {student.last_name}")
        
        # Step 2: Check if User account exists for this student
        user_exists = User.objects.filter(student=student).exists()
        
        if user_exists:
            user = User.objects.get(student=student)
            cache_user_auth(user, timeout=1800)
            # Case A: User account exists - needs login
            print(f"🔑 User account exists for {student.unique_id} - directing to login")
            return Response({
                'status': 'needs_login',
                'message': f'Welcome back, {student.first_name}! Please enter your password.',
                'student_info': {
                    'name': f"{student.first_name} {student.last_name}",
                    'university': student.university.name,
                    'student_id': student.unique_id,
                    'email': student.email
                }
            }, status=status.HTTP_200_OK)
        
        else:
            # Case B: Student exists but no User account - needs password setup
            print(f"🆕 New user detected for {student.unique_id} - directing to password setup")
            return Response({
                'status': 'needs_password_setup',
                'message': f'Welcome to University Chat, {student.first_name}! Please set up your password to get started.',
                'student_info': {
                    'name': f"{student.first_name} {student.last_name}",
                    'university': student.university.name,
                    'student_id': student.unique_id,
                    'email': student.email
                }
            }, status=status.HTTP_200_OK)
            
    except Student.DoesNotExist:
        # Case C: Email not found in student database
        print(f"❌ Student not found for email: {email}")
        return Response({
            'status': 'invalid_email',
            'message': 'This email is not registered with any university in our system. Please contact your university administrator.'
        }, status=status.HTTP_404_NOT_FOUND)
    
    except Exception as e:
        # Unexpected error
        print(f"💥 Unexpected error in check_email: {str(e)}")
        return Response({
            'error': 'An unexpected error occurred. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# Add this to your apps/authentication/views.py (after the check_email function)

from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken


@api_view(['POST'])
@permission_classes([AllowAny])
def set_password(request):
    """
    Smart Authentication Step 2A: Password Setup for First-Time Users
    
    How it works:
    1. Frontend shows password form (from check_email response)
    2. User enters email + password + confirm_password
    3. API validates password strength
    4. Creates User account linked to Student
    5. Generates JWT tokens
    6. Returns tokens + user info
    
    API Call:
    POST /api/v1/auth/set-password/
    {
        "email": "rjoshi@abc.edu",
        "password": "SecurePassword123!",
        "confirm_password": "SecurePassword123!"
    }
    
    Response:
    - Success: User data + JWT tokens
    - Error: Validation errors or account already exists
    """
    
    # Get data from request
    email = request.data.get('email', '').lower().strip()
    password = request.data.get('password', '')
    confirm_password = request.data.get('confirm_password', '')
    
    # Basic validation
    if not email or not password or not confirm_password:
        return Response({
            'error': 'Email, password, and confirm_password are required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    if password != confirm_password:
        return Response({
            'error': 'Passwords do not match'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Validate password strength using Django validators
    try:
        validate_password(password)
    except ValidationError as e:
        return Response({
            'error': 'Password validation failed',
            'details': list(e.messages)
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Get the student record
        student = Student.objects.select_related('university').get(email=email)
        print(f"✅ Found student for password setup: {student.unique_id}")
        
        # Check if User already exists (prevent duplicate accounts)
        if User.objects.filter(student=student).exists():
            print(f"⚠️ User account already exists for: {student.unique_id}")
            return Response({
                'error': 'User account already exists. Please use login instead.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create User account with atomic transaction (all-or-nothing)
        with transaction.atomic():
            # Create the user account
            user = User.objects.create_user(
                username=student.email.split('@')[0],  # Will be auto-adjusted if duplicate
                email=student.email,
                password=password,
                first_name=student.first_name,
                last_name=student.last_name,
                student=student
            )
            
            # Set password_set_at timestamp (track when password was first set)
            user.password_set_at = timezone.now()
            user.save()
            
            print(f"✅ User account created successfully for: {student.unique_id}")
            # Cache user immediately after account creation
            cache_user_auth(user, timeout=1800)  # Cache for 30 minutes
            
            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)
            access_token = refresh.access_token
            
            print(f"🔑 JWT tokens generated for: {student.unique_id}")
            
            # Return success response with user data and tokens
            return Response({
                'message': f'Welcome to University Chat, {student.first_name}! Your account has been created successfully.',
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'name': f"{user.first_name} {user.last_name}",
                    'student_id': student.unique_id,
                    'university': student.university.name,
                    'university_domain': student.university.domain,
                    'password_set_at': user.password_set_at.isoformat()
                },
                'tokens': {
                    'access': str(access_token),
                    'refresh': str(refresh)
                }
            }, status=status.HTTP_201_CREATED)
            
    except Student.DoesNotExist:
        print(f"❌ Student not found for email during password setup: {email}")
        return Response({
            'error': 'Invalid email. Student record not found.'
        }, status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        print(f"💥 Error creating user account: {str(e)}")
        return Response({
            'error': 'Failed to create account. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Add this to your apps/authentication/views.py (after the set_password function)

from django.contrib.auth import authenticate


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    """
    Smart Authentication Step 2B: Login for Existing Users
    
    How it works:
    1. Frontend shows login form (from check_email response)
    2. User enters email + password
    3. API authenticates credentials
    4. Generates fresh JWT tokens
    5. Returns tokens + user info
    
    API Call:
    POST /api/v1/auth/login/
    {
        "email": "rjoshi@abc.edu",
        "password": "SecurePassword123!"
    }
    
    Response:
    - Success: User data + JWT tokens
    - Error: Invalid credentials
    """
    
    # Get data from request
    print(f"🔍 Login attempt received")
    email = request.data.get('email', '').lower().strip()
    password = request.data.get('password', '')
    
    # Basic validation
    if not email or not password:
        return Response({
            'error': 'Email and password are required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Authenticate user credentials
        # Django will use your custom USERNAME_FIELD (email) for authentication
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            # Authentication successful
            if user.is_active:
                # Handle both admin and student users
                user_info = user.student.unique_id if user.student else "admin"
                print(f"✅ Successful login for: {user_info}")
                 # Cache user data after successful login
                cache_user_auth(user, timeout=1800)  # Cache for 30 minutes
                
                # Generate fresh JWT tokens
                refresh = RefreshToken.for_user(user)
                access_token = refresh.access_token
                
                print(f"🔑 Fresh JWT tokens generated for: {user_info}")
                
                # Build response data
                response_data = {
                    'message': f'Welcome back, {user.first_name}!',
                    'user': {
                        'id': user.id,
                        'email': user.email,
                        'name': f"{user.first_name} {user.last_name}",
                        'last_login': user.last_login.isoformat() if user.last_login else None,
                        'password_set_at': user.password_set_at.isoformat() if user.password_set_at else None
                    },
                    'tokens': {
                        'access': str(access_token),
                        'refresh': str(refresh)
                    }
                }
                
                # Add student info only if user has a student record
                if user.student:
                    response_data['user']['student_id'] = user.student.unique_id
                    response_data['user']['university'] = user.student.university.name
                    response_data['user']['university_domain'] = user.student.university.domain
                else:
                    response_data['user']['user_type'] = 'admin'
                
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # User account is deactivated
                print(f"⚠️ Login attempt for deactivated account: {email}")
                return Response({
                    'error': 'Your account has been deactivated. Please contact support.'
                }, status=status.HTTP_401_UNAUTHORIZED)
        else:
            # Authentication failed - wrong password or email not found
            print(f"❌ Failed login attempt for: {email}")
            return Response({
                'error': 'Invalid email or password. Please check your credentials and try again.'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
    except Exception as e:
        print(f"💥 Unexpected error during login: {str(e)}")
        return Response({
            'error': 'Login failed. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#ADMIN AUTHENTICATION END POINTS


@api_view(['POST'])
@permission_classes([IsAdminUser])
def add_university(request):
    """
    Add new university with 3-letter code
    
    POST /api/v1/auth/admin/add-university/
    {
        "university_code": "xyz",
        "university_name": "XYZ College"
    }
    """
    
    university_code = request.data.get('university_code', '').lower().strip()
    university_name = request.data.get('university_name', '').strip()
    
    # Validate inputs
    if not university_code or not university_name:
        return Response({
            'error': 'Both university_code and university_name are required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Validate 3-letter code
    if not re.match(r'^[a-z]{3}$', university_code):
        return Response({
            'error': 'University code must be exactly 3 letters (a-z only)'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Check if university exists
    domain = f"{university_code}.edu"
    from .models import University
    
    if University.objects.filter(domain=domain).exists():
        return Response({
            'error': f'University with code "{university_code}" already exists'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    if University.objects.filter(name__iexact=university_name).exists():
        return Response({
            'error': f'University with name "{university_name}" already exists'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Create university
        university = University.objects.create(
            name=university_name,
            domain=domain,
            is_active=True
        )
        
        print(f"University created: {university.name} ({university.domain})")
        
        return Response({
            'message': f'University "{university.name}" created successfully',
            'university': {
                'id': university.id,
                'name': university.name,
                'domain': university.domain,
                'created_at': university.created_at.isoformat()
            }
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        print(f"Error creating university: {str(e)}")
        return Response({
            'error': 'Failed to create university. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def upload_students(request):
    """
    Upload students via CSV to existing university
    
    POST /api/v1/auth/admin/upload-students/
    {
        "university_id": 1,
        "csv_data": "first_name,last_name\nJohn,Doe\nJane,Smith"
    }
    
    Or with university domain:
    {
        "university_domain": "abc.edu",
        "csv_data": "first_name,last_name\nJohn,Doe\nJane,Smith"
    }
    """
    
    university_id = request.data.get('university_id')
    university_domain = request.data.get('university_domain')
    csv_data = request.data.get('csv_data', '').strip()
    
    # Validate inputs
    if not (university_id or university_domain):
        return Response({
            'error': 'Either university_id or university_domain is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    if not csv_data:
        return Response({
            'error': 'csv_data is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Get university
       
        
        if university_id:
            university = University.objects.get(id=university_id, is_active=True)
        else:
            university = University.objects.get(domain=university_domain, is_active=True)
            
        print(f"Processing students for: {university.name}")
        
    except University.DoesNotExist:
        return Response({
            'error': 'University not found or inactive'
        }, status=status.HTTP_404_NOT_FOUND)
    
    try:
        # Process CSV
        created_count = process_student_csv(csv_data, university)
        
        print(f"Created {created_count} students for {university.name}")
        
        return Response({
            'message': f'Successfully created {created_count} students for {university.name}',
            'university': university.name,
            'students_created': created_count,
            'next_student_id': get_next_student_id(university)
        }, status=status.HTTP_201_CREATED)
        
    except ValidationError as e:
        return Response({
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        print(f"Error processing CSV: {str(e)}")
        return Response({
            'error': 'Failed to process CSV data. Please check format and try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def list_universities(request):
    """
    List all universities for dropdown selection
    
    GET /api/v1/auth/admin/universities/
    """
    

    
    universities = University.objects.filter(is_active=True).order_by('name')
    
    university_list = []
    for university in universities:
        student_count = university.students.count()
        university_list.append({
            'id': university.id,
            'name': university.name,
            'domain': university.domain,
            'student_count': student_count,
            'next_student_id': get_next_student_id(university)
        })
    
    return Response({
        'universities': university_list,
        'total_count': len(university_list)
    })


# ADD THESE HELPER FUNCTIONS at the end of your views.py file

def process_student_csv(csv_data, university):
    """Process CSV data and create students"""
    
    # Parse CSV
    csv_reader = csv.DictReader(io.StringIO(csv_data))
    
    # Validate headers
    required_fields = {'first_name', 'last_name'}
    if not required_fields.issubset(set(csv_reader.fieldnames)):
        raise ValidationError(f'CSV must contain columns: {", ".join(required_fields)}')
    
    students_to_create = []
    
    # Validate data
    for row_num, row in enumerate(csv_reader, start=2):
        first_name = row['first_name'].strip()
        last_name = row['last_name'].strip()
        
        if not first_name or not last_name:
            continue  # Skip empty rows
        
        # Validate name format
        if not re.match(r'^[a-zA-Z\s\-\'\.]+$', first_name) or not re.match(r'^[a-zA-Z\s\-\'\.]+$', last_name):
            raise ValidationError(f'Row {row_num}: Names can only contain letters, spaces, hyphens, apostrophes, and periods')
        
        students_to_create.append({
            'first_name': first_name,
            'last_name': last_name,
            'row_num': row_num
        })
    
    if not students_to_create:
        raise ValidationError('No valid student records found in CSV')
    
    # Create students in transaction
    created_count = 0
    
    with transaction.atomic():
        for student_data in students_to_create:
            # Generate unique ID
            unique_id = get_next_student_id(university)
            
            # Create student
 
            student, created = Student.objects.get_or_create(
                unique_id=unique_id,
                defaults={
                    'first_name': student_data['first_name'],
                    'last_name': student_data['last_name'],
                    'university': university
                }
            )
            
            if created:
                created_count += 1
                print(f"Created: {student.unique_id} - {student.first_name} {student.last_name} ({student.email})")
    
    return created_count


def get_next_student_id(university):
    """Generate next available student ID for university"""
    domain_prefix = university.domain.split('.')[0].lower()
    
 
    existing_students = Student.objects.filter(
        university=university,
        unique_id__startswith=domain_prefix
    ).order_by('unique_id')
    
    if not existing_students.exists():
        return f"{domain_prefix}1"
    
    # Find highest number
    highest_num = 0
    pattern = re.compile(f'^{domain_prefix}(\d+)$')
    
    for student in existing_students:
        match = pattern.match(student.unique_id)
        if match:
            num = int(match.group(1))
            highest_num = max(highest_num, num)
    
    return f"{domain_prefix}{highest_num + 1}"


#logout endpoint
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """
    Logout endpoint - Blacklist refresh token
    
    POST /api/v1/auth/logout/
    {
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    }
    
    Response:
    - Success: Logout confirmation message
    - Error: Invalid/expired token
    """
    
    try:
        refresh_token = request.data.get('refresh_token')
        
        if not refresh_token:
            return Response({
                'error': 'Refresh token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        # Invalidate user cache on logout
        invalidate_user_auth_cache(request.user.id)
        # Blacklist the refresh token
        token = RefreshToken(refresh_token)
        token.blacklist()
        
        # Log successful logout
        user_info = "admin" if not request.user.student else request.user.student.unique_id
        print(f"User logged out successfully: {user_info}")
        
        return Response({
            'message': 'Logged out successfully. See you soon!'
        }, status=status.HTTP_200_OK)
        
    except TokenError as e:
        print(f"Token error during logout: {str(e)}")
        return Response({
            'error': 'Invalid or expired token'
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        print(f"Unexpected error during logout: {str(e)}")
        return Response({
            'error': 'Logout failed. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)