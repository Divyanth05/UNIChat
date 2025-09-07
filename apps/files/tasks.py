import os
import uuid
import logging
from celery import shared_task
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from apps.files.models import ChatFile
from apps.chat.models import Conversation, Message
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)

@shared_task(bind=True, queue='file_processing')
def process_file_upload(self, temp_file_path, conversation_id, user_id, file_data):
    """
    Background task to process file upload - INVISIBLE to other users until complete
    
    Args:
        temp_file_path: Path to temporarily saved file
        conversation_id: UUID of conversation
        user_id: ID of user who uploaded
        file_data: Dict with file metadata
    """
    try:
        logger.info(f"Starting file processing for user {user_id}: {file_data['original_name']}")
        
        # 1. Validate inputs
        conversation = Conversation.objects.get(id=conversation_id)
        user = User.objects.get(id=user_id)
        
        # 2. Upload file to S3/storage
        file_url = upload_file_to_storage(temp_file_path, file_data)
        
        # 3. No thumbnail generation (Pillow not installed)
        thumbnail_url = None
        
        # 4. Create ChatFile record
        chat_file = ChatFile.objects.create(
            original_name=file_data['original_name'],
            file_name=file_data['unique_filename'],
            file_size=file_data['file_size'],
            file_type=file_data['file_type'],
            mime_type=file_data['mime_type'],
            file_url=file_url,
            thumbnail_url=thumbnail_url,
            uploaded_by=user,
            conversation=conversation,
            upload_complete=True,
            processing_complete=True
        )
        
        # 5. Create message record
        message = Message.objects.create(
            conversation=conversation,
            sender=user,
            content=f"📎 {file_data['original_name']}",
            message_type='file',
            file_url=file_url,
            file_name=file_data['original_name'],
            file_size=file_data['file_size']
        )
        
        # 6. INVISIBLE UPLOAD: Only NOW broadcast file message to conversation
        broadcast_file_message_to_conversation(conversation_id, message, chat_file)
        
        # 7. Cleanup temporary file
        cleanup_temp_file(temp_file_path)
        
        logger.info(f"File processing completed successfully: {file_data['original_name']}")
        
        return {
            'status': 'success',
            'message_id': str(message.id),
            'file_url': file_url,
            'chat_file_id': str(chat_file.id)
        }
        
    except Exception as e:
        logger.error(f"File processing failed: {str(e)}")
        
        # Notify only the uploader about failure (invisible to others)
        notify_upload_failure(user_id, file_data['original_name'], str(e))
        
        # Cleanup on failure
        cleanup_temp_file(temp_file_path)
        
        # Retry the task
        raise self.retry(countdown=60, max_retries=3)

def upload_file_to_storage(temp_file_path, file_data):
    """Upload file from temp location to S3/storage"""
    try:
        with open(temp_file_path, 'rb') as temp_file:
            file_content = ContentFile(temp_file.read())
            
        # Generate storage path
        storage_path = f"chat_files/{file_data['unique_filename']}"
        
        # Save to storage (S3 or local)
        saved_path = default_storage.save(storage_path, file_content)
        
        # Get the public URL
        file_url = default_storage.url(saved_path)
        
        logger.info(f"File uploaded to storage: {saved_path}")
        return file_url
        
    except Exception as e:
        logger.error(f"Storage upload failed: {str(e)}")
        raise

def broadcast_file_message_to_conversation(conversation_id, message, chat_file):
    """Broadcast completed file message to all conversation members - INVISIBLE UPLOAD"""
    try:
        channel_layer = get_channel_layer()
        group_name = f"conversation_{conversation_id}"
        
        # Prepare message data (appears as regular message - NOT upload status)
        message_data = {
            'id': str(message.id),
            'conversation_id': str(message.conversation.id),
            'sender': {
                'id': message.sender.id,
                'email': message.sender.email,
                'full_name': f"{message.sender.first_name} {message.sender.last_name}".strip() or message.sender.email.split('@')[0]
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
        
        # Send to WebSocket group as REGULAR message (not upload status)
        async_to_sync(channel_layer.group_send)(group_name, {
            'type': 'new_message',  # Regular message type - invisible upload!
            'message': message_data
        })
        
        logger.info(f"File message broadcasted to conversation {conversation_id}")
        
    except Exception as e:
        logger.error(f"Error broadcasting file message: {str(e)}")

def notify_upload_failure(user_id, filename, error_message):
    """Notify only the uploader about upload failure (private notification)"""
    try:
        channel_layer = get_channel_layer()
        
        # Send private notification to uploader only
        async_to_sync(channel_layer.group_send)(f"user_{user_id}", {
            'type': 'upload_failed',
            'data': {
                'filename': filename,
                'error': error_message,
                'timestamp': timezone.now().isoformat()
            }
        })
        
        logger.info(f"Upload failure notification sent to user {user_id}")
        
    except Exception as e:
        logger.error(f"Error sending failure notification: {str(e)}")

def cleanup_temp_file(temp_file_path):
    """Clean up temporary file"""
    try:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            logger.info(f"Temporary file cleaned up: {temp_file_path}")
    except Exception as e:
        logger.error(f"Error cleaning up temp file: {str(e)}")

@shared_task(queue='cleanup')
def cleanup_old_temp_files():
    """Periodic task to clean up old temporary files"""
    temp_dir = getattr(settings, 'CELERY_TEMP_FILE_DIR', '/tmp')
    
    try:
        import time
        current_time = time.time()
        
        for filename in os.listdir(temp_dir):
            if filename.startswith('temp_upload_'):
                file_path = os.path.join(temp_dir, filename)
                file_age = current_time - os.path.getmtime(file_path)
                
                # Delete files older than 1 hour
                if file_age > 3600:
                    os.remove(file_path)
                    logger.info(f"Cleaned up old temp file: {filename}")
                    
    except Exception as e:
        logger.error(f"Error during temp file cleanup: {str(e)}")