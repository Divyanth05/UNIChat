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

from .models import ChatFile
from apps.chat.models import Conversation, ConversationMembership, Message

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_file(request):
    """
    Upload file to conversation
    
    POST /api/v1/files/upload/
    Content-Type: multipart/form-data
    
    Form data:
    - file: File to upload
    - conversation_id: UUID of conversation
    - message_content: Optional message text (default: filename)
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
        
        # Process file upload
        with transaction.atomic():
            # Generate unique filename
            file_extension = os.path.splitext(uploaded_file.name)[1].lower()
            unique_filename = f"{uuid.uuid4().hex}{file_extension}"
            
            # Determine file type
            mime_type = validation_result['mime_type']
            file_type = get_file_type_from_mime(mime_type)
            
            # Save file to S3
            file_path = f"chat_files/{unique_filename}"
            saved_path = default_storage.save(file_path, uploaded_file)
            file_url = default_storage.url(saved_path)
            
            # Create thumbnail for images (optional)
            thumbnail_url = None
            if file_type == 'image':
                try:
                    thumbnail_url = create_image_thumbnail(uploaded_file, unique_filename)
                except Exception as e:
                    print(f"Thumbnail creation failed: {str(e)}")
            
            # Create ChatFile record
            chat_file = ChatFile.objects.create(
                original_name=uploaded_file.name,
                file_name=unique_filename,
                file_size=uploaded_file.size,
                file_type=file_type,
                mime_type=mime_type,
                file_url=file_url,
                thumbnail_url=thumbnail_url,
                uploaded_by=request.user,
                conversation=conversation,
                upload_complete=True
            )
            
            # Create message with file attachment
            if not message_content:
                message_content = f"📎 {uploaded_file.name}"
            
            message = Message.objects.create(
                conversation=conversation,
                sender=request.user,
                content=message_content,
                message_type='file',
                file_url=file_url,
                file_name=uploaded_file.name,
                file_size=uploaded_file.size,
                file_type=mime_type
            )
            
            print(f"File uploaded: {uploaded_file.name} by {request.user.email}")
        
        # Broadcast file message via WebSocket
        broadcast_file_message(message, chat_file)
        
        return Response({
            'message': 'File uploaded successfully',
            'file': {
                'id': str(chat_file.id),
                'original_name': chat_file.original_name,
                'file_size': chat_file.file_size,
                'file_size_human': chat_file.file_size_human,
                'file_type': chat_file.file_type,
                'mime_type': chat_file.mime_type,
                'file_url': chat_file.file_url,
                'thumbnail_url': chat_file.thumbnail_url
            },
            'message_data': {
                'id': str(message.id),
                'content': message.content,
                'timestamp': message.timestamp.isoformat()
            }
        }, status=status.HTTP_201_CREATED)
        
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


def create_image_thumbnail(image_file, unique_filename):
    """Create thumbnail for image files (optional - requires Pillow)"""
    try:
        from PIL import Image
        from io import BytesIO
        from django.core.files.base import ContentFile
        
        # Open image
        img = Image.open(image_file)
        
        # Create thumbnail
        img.thumbnail((300, 300), Image.Resampling.LANCZOS)
        
        # Save thumbnail to bytes
        thumb_io = BytesIO()
        img_format = img.format or 'JPEG'
        img.save(thumb_io, format=img_format, quality=85)
        thumb_io.seek(0)
        
        # Upload thumbnail to S3
        thumb_filename = f"thumb_{unique_filename}"
        thumb_path = f"thumbnails/{thumb_filename}"
        thumb_file = ContentFile(thumb_io.getvalue())
        
        saved_thumb_path = default_storage.save(thumb_path, thumb_file)
        thumbnail_url = default_storage.url(saved_thumb_path)
        
        return thumbnail_url
        
    except ImportError:
        print("Pillow not installed - skipping thumbnail generation")
        return None
    except Exception as e:
        print(f"Thumbnail creation error: {str(e)}")
        return None


def broadcast_file_message(message, chat_file):
    """Broadcast file message to all conversation members via WebSocket"""
    try:
        channel_layer = get_channel_layer()
        group_name = f"conversation_{message.conversation.id}"
        
        # Prepare message data for WebSocket
        message_data = {
            'id': str(message.id),
            'conversation_id': str(message.conversation.id),
            'sender': {
                'id': message.sender.id,
                'email': message.sender.email,
                'full_name': f"{message.sender.first_name} {message.sender.last_name}".strip()
            },
            'content': message.content,
            'message_type': message.message_type,
            'timestamp': message.timestamp.isoformat(),
            'file_data': {
                'id': str(chat_file.id),
                'original_name': chat_file.original_name,
                'file_size': chat_file.file_size,
                'file_size_human': chat_file.file_size_human,
                'file_type': chat_file.file_type,
                'mime_type': chat_file.mime_type,
                'file_url': chat_file.file_url,
                'thumbnail_url': chat_file.thumbnail_url
            }
        }
        
        # Send to WebSocket group
        async_to_sync(channel_layer.group_send)(group_name, {
            'type': 'file_message',
            'message': message_data
        })
        
        print(f"File message broadcasted to conversation {message.conversation.id}")
        
    except Exception as e:
        print(f"Error broadcasting file message: {str(e)}")

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_file(request, file_id):
    """
    Get file download URL with permission check
    
    GET /api/v1/files/{file_id}/download/
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