# apps/files/views.py
import os
import uuid
import mimetypes
from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings

from .models import ChatFile
from apps.chat.models import Conversation, ConversationMembership, Message
from .tasks import process_file_upload  # Import Celery task

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_file(request):
    """
    Upload file to conversation with Celery background processing
    
    POST /api/v1/files/upload/
    Content-Type: multipart/form-data
    
    Form data:
    - file: File to upload
    - conversation_id: UUID of conversation
    - message_content: Optional message text (default: filename)
    
    NEW BEHAVIOR: 
    - Returns immediately after validation and temp storage
    - File processing happens in background via Celery
    - Other users see file only when fully processed (invisible upload)
    """
    try:
        # Get form data
        uploaded_file = request.FILES.get('file')
        conversation_id = request.data.get('conversation_id')
        message_content = request.data.get('message_content', '').strip()
        
        # Validate inputs
        if not uploaded_file:
            return Response({
                'error': 'No file provided'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not conversation_id:
            return Response({
                'error': 'conversation_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check conversation membership
        conversation = get_object_or_404(Conversation, id=conversation_id)
        membership = ConversationMembership.objects.filter(
            conversation=conversation,
            user=request.user,
            is_active=True
        ).first()
        
        if not membership:
            return Response({
                'error': 'You are not a member of this conversation'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Validate file
        validation_result = validate_file(uploaded_file)
        if not validation_result['valid']:
            return Response({
                'error': validation_result['error']
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # FAST OPERATIONS: Prepare for background processing
        
        # Generate unique filename
        file_extension = os.path.splitext(uploaded_file.name)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}{file_extension}"
        
        # Determine file type
        mime_type = validation_result['mime_type']
        file_type = get_file_type_from_mime(mime_type)
        
        # FAST: Save file to temporary location (0.1 seconds)
        temp_uploads_dir = getattr(settings, 'CELERY_TEMP_FILE_DIR', settings.BASE_DIR / 'temp_uploads')
        os.makedirs(temp_uploads_dir, exist_ok=True)
        
        temp_filename = f"temp_upload_{uuid.uuid4().hex}{file_extension}"
        temp_file_path = os.path.join(temp_uploads_dir, temp_filename)
        
        # Save uploaded file to temporary location
        with open(temp_file_path, 'wb') as temp_file:
            for chunk in uploaded_file.chunks():
                temp_file.write(chunk)
        
        # Prepare file metadata for background task
        file_data = {
            'original_name': uploaded_file.name,
            'unique_filename': unique_filename,
            'file_size': uploaded_file.size,
            'file_type': file_type,
            'mime_type': mime_type,
            'message_content': message_content or f"📎 {uploaded_file.name}"
        }
        
        # FAST: Queue background task (0.01 seconds)
        task = process_file_upload.delay(
            temp_file_path=temp_file_path,
            conversation_id=str(conversation_id),
            user_id=request.user.id,
            file_data=file_data
        )
        
        print(f"File upload queued: {uploaded_file.name} by {request.user.email} (Task ID: {task.id})")
        
        # IMMEDIATE RESPONSE: User gets response in ~0.12 seconds
        return Response({
            'status': 'upload_started',
            'message': f'File "{uploaded_file.name}" is being processed in background...',
            'task_id': task.id,
            'file_info': {
                'original_name': uploaded_file.name,
                'file_size': uploaded_file.size,
                'file_size_human': f"{uploaded_file.size / (1024*1024):.1f} MB" if uploaded_file.size > 1024*1024 else f"{uploaded_file.size / 1024:.1f} KB",
                'file_type': file_type,
                'mime_type': mime_type
            }
        }, status=status.HTTP_202_ACCEPTED)  # 202 = Accepted, Processing
        
    except Exception as e:
        print(f"File upload error: {str(e)}")
        return Response({
            'error': 'File upload failed. Please try again.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def validate_file(uploaded_file):
    """Validate uploaded file"""
    
    # File size limits (10MB max, 5MB for images)
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_IMAGE_SIZE = 5 * 1024 * 1024   # 5MB
    
    # Check file size
    if uploaded_file.size > MAX_FILE_SIZE:
        return {
            'valid': False,
            'error': f'File size ({uploaded_file.size / (1024*1024):.1f}MB) exceeds maximum allowed size (10MB)'
        }
    
    # Get MIME type
    mime_type, _ = mimetypes.guess_type(uploaded_file.name)
    if not mime_type:
        mime_type = 'application/octet-stream'
    
    # Allowed MIME types
    ALLOWED_MIME_TYPES = {
        # Images
        'image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp',
        # Documents
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/plain', 'text/csv'
    }
    
    if mime_type not in ALLOWED_MIME_TYPES:
        return {
            'valid': False,
            'error': f'File type {mime_type} is not allowed. Only images and documents are supported.'
        }
    
    # Additional size check for images
    if mime_type.startswith('image/') and uploaded_file.size > MAX_IMAGE_SIZE:
        return {
            'valid': False,
            'error': f'Image size ({uploaded_file.size / (1024*1024):.1f}MB) exceeds maximum allowed size for images (5MB)'
        }
    
    return {
        'valid': True,
        'mime_type': mime_type
    }


def get_file_type_from_mime(mime_type):
    """Determine file type category from MIME type"""
    if mime_type.startswith('image/'):
        return 'image'
    else:
        return 'document'




@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_file(request, file_id):
    """
    Get file download URL with permission check
    
    GET /api/v1/files/{file_id}/download/
    
    UNCHANGED: This function works exactly the same with Celery
    """
    try:
        chat_file = get_object_or_404(ChatFile, id=file_id)
        
        # Check if user has permission to access this file
        membership = ConversationMembership.objects.filter(
            conversation=chat_file.conversation,
            user=request.user,
            is_active=True
        ).first()
        
        if not membership:
            return Response({
                'error': 'You do not have permission to access this file'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Return file information and download URL
        return Response({
            'file': {
                'id': str(chat_file.id),
                'original_name': chat_file.original_name,
                'file_size': chat_file.file_size,
                'file_size_human': chat_file.file_size_human,
                'file_type': chat_file.file_type,
                'mime_type': chat_file.mime_type,
                'file_url': chat_file.file_url,  # S3 URL for direct download
                'thumbnail_url': chat_file.thumbnail_url,
                'uploaded_by': {
                    'id': chat_file.uploaded_by.id,
                    'email': chat_file.uploaded_by.email,
                    'name': f"{chat_file.uploaded_by.first_name} {chat_file.uploaded_by.last_name}".strip()
                },
                'uploaded_at': chat_file.uploaded_at.isoformat()
            }
        })
        
    except Exception as e:
        print(f"File download error: {str(e)}")
        return Response({
            'error': 'Failed to access file'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def upload_status(request, task_id):
    """
    Check status of background file upload task
    
    GET /api/v1/files/upload-status/{task_id}/
    
    NEW ENDPOINT: Check if background upload is complete
    """
    try:
        from celery.result import AsyncResult
        
        task_result = AsyncResult(task_id)
        
        if task_result.state == 'PENDING':
            return Response({
                'status': 'processing',
                'message': 'File is still being processed...'
            })
        elif task_result.state == 'SUCCESS':
            return Response({
                'status': 'completed',
                'message': 'File uploaded successfully!',
                'result': task_result.result
            })
        elif task_result.state == 'FAILURE':
            return Response({
                'status': 'failed', 
                'message': 'File upload failed',
                'error': str(task_result.info)
            })
        else:
            return Response({
                'status': task_result.state,
                'message': f'Upload status: {task_result.state}'
            })
            
    except Exception as e:
        return Response({
            'error': 'Failed to check upload status'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)