
import uuid
import os
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

class ChatFile(models.Model):
    """
    Model for managing file uploads in chat conversations
    """
    FILE_TYPES = [
        ('image', 'Image'),
        ('document', 'Document'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('archive', 'Archive'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # File information
    original_name = models.CharField(max_length=255, help_text="Original filename from user")
    file_name = models.CharField(max_length=255, help_text="Stored filename (UUID-based)")
    file_size = models.PositiveIntegerField(help_text="File size in bytes")
    file_type = models.CharField(max_length=20, choices=FILE_TYPES)
    mime_type = models.CharField(max_length=100, help_text="MIME type (e.g., image/jpeg)")
    
    # S3 URLs
    file_url = models.URLField(help_text="S3 URL to access file")
    thumbnail_url = models.URLField(blank=True, null=True, help_text="Thumbnail URL for images")
    
    # Upload information
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='uploaded_files')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    # Conversation context
    conversation = models.ForeignKey(
        'chat.Conversation',  # Reference to chat app's Conversation model
        on_delete=models.CASCADE,
        related_name='files',
        help_text="Which conversation this file belongs to"
    )
    
    # Status tracking
    upload_complete = models.BooleanField(default=False, help_text="Upload finished successfully")
    processing_complete = models.BooleanField(default=True, help_text="File processing done")
    
    class Meta:
        db_table = 'chat_files'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['conversation', 'uploaded_at']),
            models.Index(fields=['uploaded_by', 'uploaded_at']),
            models.Index(fields=['file_type']),
        ]
    
    def __str__(self):
        return f"{self.original_name} ({self.get_file_type_display()})"
    
    @property
    def file_size_human(self):
        """Return human-readable file size"""
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    
    def get_file_category(self):
        """Determine file category from MIME type"""
        if self.mime_type.startswith('image/'):
            return 'image'
        elif self.mime_type in [
            'application/pdf', 'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain', 'text/csv'
        ]:
            return 'document'
        else:
            return 'document'  # Default to document for unsupported types