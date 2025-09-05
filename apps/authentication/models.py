"""
Authentication models for university chat application.
Updated to support pre-populated students with auto-generated emails.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import EmailValidator
from django.utils.translation import gettext_lazy as _
import re


class University(models.Model):
    """University model for managing university information."""
    
    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Full name of the university"
    )
    domain = models.CharField(
        max_length=100,
        unique=True,
        help_text="Email domain for the university (e.g., 'abc.edu')"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this university is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'universities'
        verbose_name = 'University'
        verbose_name_plural = 'Universities'
        ordering = ['name']

    def __str__(self):
        return self.name

    def clean(self):
        """Validate university data."""
        from django.core.exceptions import ValidationError
        
        if self.domain:
            self.domain = self.domain.lower().strip()
            if self.domain.startswith('@'):
                raise ValidationError({'domain': 'Domain should not start with @'})
            if '://' in self.domain:
                raise ValidationError({'domain': 'Domain should not include protocol'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Student(models.Model):
    """
    Student model for pre-populated student data.
    Contains basic student information before account creation.
    Uses college ID as primary key.
    """
    
    unique_id = models.CharField(
        max_length=50,
        primary_key=True,
        help_text="College/University student ID (e.g., 'abc1')"
    )
    first_name = models.CharField(
        max_length=150,
        help_text="Student's first name"
    )
    last_name = models.CharField(
        max_length=150,
        help_text="Student's last name"
    )
    email = models.EmailField(
        unique=True,
        validators=[EmailValidator()],
        help_text="Auto-generated university email"
    )
    university = models.ForeignKey(
        University,
        on_delete=models.PROTECT,
        related_name='students',
        help_text='University this student belongs to'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'students'
        verbose_name = 'Student'
        verbose_name_plural = 'Students'
        ordering = ['unique_id']

    def __str__(self):
        return f"{self.unique_id}: {self.first_name} {self.last_name} ({self.email})"

    @staticmethod
    def clean_name_for_email(name):
        """Clean name for email generation - remove special characters."""
        return re.sub(r'[^a-zA-Z]', '', name).lower()

    @classmethod
    def generate_email(cls, first_name, last_name, university_domain):
        """
        Generate email: first_letter_of_first_name + last_name + @domain
        Handle duplicates by appending numbers (rjoshi@abc.edu, rjoshi2@abc.edu)
        """
        first_initial = cls.clean_name_for_email(first_name)[0] if first_name else 'x'
        clean_last_name = cls.clean_name_for_email(last_name)
        
        # Base email format
        base_username = f"{first_initial}{clean_last_name}"
        base_email = f"{base_username}@{university_domain}"
        
        # Check if email already exists
        if not cls.objects.filter(email=base_email).exists():
            return base_email
        
        # Handle duplicates - append numbers
        counter = 2
        while True:
            new_email = f"{base_username}{counter}@{university_domain}"
            if not cls.objects.filter(email=new_email).exists():
                return new_email
            counter += 1

    def save(self, *args, **kwargs):
        # Auto-generate email if not provided
        if not self.email and self.first_name and self.last_name and self.university:
            self.email = self.generate_email(
                self.first_name, 
                self.last_name, 
                self.university.domain
            )
        super().save(*args, **kwargs)


class User(AbstractUser):
    """
    Custom User model linked to Student.
    Handles authentication and password management.
    """
    
    # Link to student record (now references unique_id)
    student = models.OneToOneField(
        Student,
        on_delete=models.CASCADE,
        null=True,         
        blank=True,
        related_name='user_account',
        help_text='Student record this user account belongs to'
        
    )
    
    # Override email to use student's email
    email = models.EmailField(
        _('email address'),
        unique=True,
        validators=[EmailValidator()],
        help_text='Email address from student record'
    )
    
    # Track password setup
    password_set_at = models.DateTimeField(
        null=True, 
        blank=True,
        help_text='When the user first set their password'
    )
    
    # Override username requirement - we use email for authentication
    username = models.CharField(
        max_length=150,
        unique=True,
        help_text='Auto-generated from student email'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Use email as the unique identifier for authentication
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-created_at']

    def __str__(self):
        if self.student:
            return f"{self.email} ({self.student.unique_id})"
        else:
            return f"{self.email} (admin)"  # For users without student records


    @property
    def has_password_set(self):
        """Check if user has set a password."""
        return self.password_set_at is not None

    @property
    def display_name(self):
        """Return the best display name for the user."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.student:
            return f"{self.student.first_name} {self.student.last_name}"
        else:
            return self.username

    def save(self, *args, **kwargs):
        """Override save to sync with student data."""
        if self.student:
            # Sync email from student
            self.email = self.student.email
            
            # Sync name from student if not set
            if not self.first_name:
                self.first_name = self.student.first_name
            if not self.last_name:
                self.last_name = self.student.last_name
            
            # Auto-generate username from email
            if not self.username:
                self.username = self.email.split('@')[0]
                
                # Handle duplicate usernames
                if User.objects.filter(username=self.username).exclude(pk=self.pk).exists():
                    import uuid
                    self.username = f"{self.username}_{uuid.uuid4().hex[:8]}"
        
        super().save(*args, **kwargs)