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
from .models import Student, User


@api_view(['POST'])
@permission_classes([AllowAny])
def check_email(request):
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
        student = Student.objects.select_related('university').get(email=email) 
        #how university name  is fetched ny using joins by django . it similar to join in sql. equilavent to student= students.objects.get(email=email) and university_name=student.university.name
        #Get the student AND their university data in ONE database query using a JOIN, so I don't have to make a second query later.
        print(f"✅ Student found: {student.unique_id} - {student.first_name} {student.last_name}")
        
        # Step 2: Check if User account exists for this student
        user_exists = User.objects.filter(student=student).exists()
        
        if user_exists:
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
                print(f"✅ Successful login for: {user.student.unique_id}")
                
                # Generate fresh JWT tokens
                refresh = RefreshToken.for_user(user)
                access_token = refresh.access_token
                
                # Update last login timestamp (Django does this automatically, but being explicit)

                
                print(f"🔑 Fresh JWT tokens generated for: {user.student.unique_id}")
                
                # Return success response
                return Response({
                    'message': f'Welcome back, {user.first_name}!',
                    'user': {
                        'id': user.id,
                        'email': user.email,
                        'name': f"{user.first_name} {user.last_name}",
                        'student_id': user.student.unique_id,
                        'university': user.student.university.name,
                        'university_domain': user.student.university.domain,
                        'last_login': user.last_login.isoformat() if user.last_login else None,
                        'password_set_at': user.password_set_at.isoformat() if user.password_set_at else None
                    },
                    'tokens': {
                        'access': str(access_token),
                        'refresh': str(refresh)
                    }
                }, status=status.HTTP_200_OK)
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
# Create your views here.
# (keeping the original comment for reference)  