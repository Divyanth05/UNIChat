import json
import logging
from datetime import datetime, timedelta
from django.db import transaction
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import (
    Conversation, ConversationMembership, Message, 
    UserPresence, TypingIndicator
)

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time chat functionality
    """
    
    async def connect(self):
        """Handle WebSocket connection"""
        try:
            # Get authenticated user from JWT middleware
            self.user = self.scope["user"]
            
            if not self.user or not self.user.is_authenticated:
                logger.warning("Unauthenticated WebSocket connection attempt")
                await self.close(code=4001)
                return
            
            # Initialize user's conversation groups
            self.user_groups = set()
            
            # Accept the connection
            await self.accept()
            
            # Set user as online
            await self.set_user_online()
            
            logger.info(f"WebSocket connected for user: {self.user.email}")
            
            # Send welcome message
            await self.send(text_data=json.dumps({
                'type': 'connection_established',
                'data': {
                    'message': 'Connected to chat server',
                    'user': {
                        'id': self.user.id,
                        'email': self.user.email,
                    }
                }
            }))
            
        except Exception as e:
            logger.error(f"WebSocket connection error: {str(e)}")
            await self.close(code=4000)
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        try:
            # Leave all conversation groups
            for group_name in list(self.user_groups):
                await self.channel_layer.group_discard(group_name, self.channel_name)
            
            # Set user offline and clear typing indicators
            if hasattr(self, 'user') and self.user.is_authenticated:
                await self.set_user_offline()
                await self.clear_all_typing_indicators()
            
            logger.info(f"WebSocket disconnected: {getattr(self.user, 'email', 'unknown')}")
            
        except Exception as e:
            logger.error(f"WebSocket disconnection error: {str(e)}")
    
    async def receive(self, text_data):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            # Route to appropriate handler
            handlers = {
                'join_conversation': self.handle_join_conversation,
                'send_message': self.handle_send_message,
                'typing_start': self.handle_typing_start,
                'typing_stop': self.handle_typing_stop,
                'ping': self.handle_ping,
                'delete_message': self.handle_delete_message,
                'react_to_message': self.handle_react_to_message,
            }
            
            handler = handlers.get(message_type)
            if handler:
                await handler(data.get('data', {}))
            else:
                await self.send_error(f"Unknown message type: {message_type}")
                
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON format")
        except Exception as e:
            logger.error(f"WebSocket receive error: {str(e)}")
            await self.send_error("Internal server error")
    
    # Message Handlers
    async def handle_ping(self, data):
        """Handle ping/keep-alive"""
        await self.send(text_data=json.dumps({
            'type': 'pong',
            'data': {'timestamp': timezone.now().isoformat()}
        }))
    
    async def handle_join_conversation(self, data):
        """Join a conversation room"""
        conversation_id = data.get('conversation_id')
        
        if not conversation_id:
            await self.send_error("conversation_id is required")
            return
        
        # Check membership
        is_member = await self.check_conversation_membership(conversation_id)
        if not is_member:
            await self.send_error("You are not a member of this conversation")
            return
        
        # Join the Redis group // creating a group , and adding this specific  user channels to the redis group 
        group_name = f"conversation_{conversation_id}"
        await self.channel_layer.group_add(group_name, self.channel_name)
        self.user_groups.add(group_name)
        
        await self.send(text_data=json.dumps({
            'type': 'conversation_joined',
            'data': {
                'conversation_id': conversation_id,
                'message': 'Successfully joined conversation'
            }
        }))
    
    async def handle_send_message(self, data):
        """Handle new message"""
        conversation_id = data.get('conversation_id')
        content = data.get('content', '').strip()
        
        if not conversation_id or not content:
            await self.send_error("conversation_id and content are required")
            return
        
        # Verify membership
        is_member = await self.check_conversation_membership(conversation_id)
        if not is_member:
            await self.send_error("You are not a member of this conversation")
            return
        
        # Create message
        message = await self.create_message(conversation_id, content)
        if not message:
            await self.send_error("Failed to create message")
            return
        
        # Clear typing indicator
        await self.clear_typing_indicator(conversation_id)
        
        # Serialize and broadcast
        message_data = await self.serialize_message(message)
        group_name = f"conversation_{conversation_id}"
        
        await self.channel_layer.group_send(group_name, {
            'type': 'new_message',
            'message': message_data
        })
    
    async def handle_typing_start(self, data):
        """Handle typing indicator start"""
        conversation_id = data.get('conversation_id')
        if not conversation_id:
            return
        
        is_member = await self.check_conversation_membership(conversation_id)
        if not is_member:
            return
        
        await self.set_typing_indicator(conversation_id)
        
        group_name = f"conversation_{conversation_id}"
        await self.channel_layer.group_send(group_name, {
            'type': 'typing_update',
            'user_id': self.user.id,
            'user_email': self.user.email,
            'conversation_id': conversation_id,
            'is_typing': True
        })
    
    async def handle_typing_stop(self, data):
        """Handle typing indicator stop"""
        conversation_id = data.get('conversation_id')
        if not conversation_id:
            return
        
        await self.clear_typing_indicator(conversation_id)
        
        group_name = f"conversation_{conversation_id}"
        await self.channel_layer.group_send(group_name, {
            'type': 'typing_update',
            'user_id': self.user.id,
            'user_email': self.user.email,
            'conversation_id': conversation_id,
            'is_typing': False
        })
    async def handle_delete_message(self, data):
        """
        Handle message deletion request via WebSocket
        
        Client sends:
        {
            "type": "delete_message",
            "data": {
                "message_id": "uuid-of-message"
            }
        }
        """
        message_id = data.get('message_id')
        
        if not message_id:
            await self.send_error("message_id is required for deletion")
            return
        
        try:
            # Delete message and get result
            result = await self.delete_message(message_id)
            
            if result['success']:
                # Send success response to the deleter
                await self.send(text_data=json.dumps({
                    'type': 'message_delete_success',
                    'data': {
                        'message_id': message_id,
                        'new_content': result['new_content'],
                        'deleted_at': result['deleted_at'],
                        'message': 'Message deleted successfully'
                    }
                }))
                
                # Broadcast deletion to all conversation members
                await self.broadcast_message_deletion(result)
                
            else:
                await self.send_error(result['error'])
                
        except Exception as e:
            logger.error(f"Error deleting message via WebSocket: {str(e)}")
            await self.send_error("Failed to delete message")


    async def handle_react_to_message(self, data):
        """
        Handle message reaction via WebSocket
        
        Client sends:
        {
            "type": "react_to_message",
            "data": {
                "message_id": "uuid-of-message",
                "reaction_type": "👍"  # Any of the allowed emoji reactions
            }
        }
        """
        message_id = data.get('message_id')
        reaction_type = data.get('reaction_type', '').strip()
        
        if not message_id or not reaction_type:
            await self.send_error("message_id and reaction_type are required")
            return
        
        try:
            # Validate reaction type
            valid_reactions = ['👍', '❤️', '😂', '😮', '😢', '😡', '👎', '🔥', '💯', '👏']
            if reaction_type not in valid_reactions:
                await self.send_error(f"Invalid reaction type. Valid options: {', '.join(valid_reactions)}")
                return
            
            # Process reaction and get result
            result = await self.toggle_message_reaction(message_id, reaction_type)
            
            if result['success']:
                # Send success response to the reactor
                await self.send(text_data=json.dumps({
                    'type': 'reaction_success',
                    'data': {
                        'message_id': message_id,
                        'action': result['action'],
                        'reaction_type': reaction_type,
                        'reaction_data': result['reaction_data'],
                        'message': f'Reaction {result["action"]} successfully'
                    }
                }))
                
                # Broadcast reaction update to all conversation members
                await self.broadcast_reaction_update(result)
                
            else:
                await self.send_error(result['error'])
                
        except Exception as e:
            logger.error(f"Error handling reaction via WebSocket: {str(e)}")
            await self.send_error("Failed to update reaction")
    

    async def handle_mark_read(self, data):
        """
        Handle marking messages as read via WebSocket
        
        Client sends:
        {
            "type": "mark_read",
            "data": {
                "conversation_id": "uuid-of-conversation",
                "message_ids": ["uuid1", "uuid2"] // Optional: specific messages
                // If no message_ids provided, marks all unread messages as read
            }
        }
        """
        conversation_id = data.get('conversation_id')
        message_ids = data.get('message_ids', [])  # Optional specific messages
        
        if not conversation_id:
            await self.send_error("conversation_id is required")
            return
        
        try:
            # Check if user is member of the conversation
            is_member = await self.check_conversation_membership(conversation_id)
            if not is_member:
                await self.send_error("You are not a member of this conversation")
                return
            
            # Mark messages as read and get result
            result = await self.mark_messages_read(conversation_id, message_ids)
            
            if result['success']:
                # Send success response to the reader
                await self.send(text_data=json.dumps({
                    'type': 'read_success',
                    'data': {
                        'conversation_id': conversation_id,
                        'messages_marked': result['messages_marked'],
                        'read_at': result['read_at'],
                        'message': f'Marked {result["messages_marked"]} messages as read'
                    }
                }))
                
                # Broadcast read receipts to authorized users
                await self.broadcast_read_receipts(result)
                
            else:
                await self.send_error(result['error'])
                
        except Exception as e:
            logger.error(f"Error marking messages as read via WebSocket: {str(e)}")
            await self.send_error("Failed to mark messages as read")



    
    # Group message receivers
    async def new_message(self, event):
        """Broadcast new message"""
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'data': event['message']
        }))
    
    async def file_message(self, event):
        """
        Broadcast file message to WebSocket clients
        Called when a file is uploaded via REST API
        """
        await self.send(text_data=json.dumps({
            'type': 'file_message',
            'data': event['message']
    }))
    
    async def message_deleted(self, event):
        """
        Receive and forward message deletion broadcast to client
        (excluding the user who deleted the message)
        """
        # Don't send deletion notification to the person who deleted it
        if event['deleted_by'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'message_deleted',
                'data': {
                    'message_id': event['message_id'],
                    'conversation_id': event['conversation_id'],
                    'deleted_by': event['deleted_by'],
                    'delete_reason': event['delete_reason'],
                    'new_content': event['new_content'],
                    'deleted_at': event['deleted_at']
                }
            }))
    
    async def reaction_update(self, event):
        """
        Receive and forward reaction update broadcast to client
        (excluding the user who made the reaction)
        """
        # Don't send reaction update to the person who made the reaction
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'reaction_update',
                'data': {
                    'message_id': event['message_id'],
                    'conversation_id': event['conversation_id'],
                    'action': event['action'],  # 'added', 'updated', 'removed'
                    'reaction_data': event['reaction_data'],
                    'user_id': event['user_id']
                }
            }))
    
    async def typing_update(self, event):
        """Broadcast typing update (exclude sender)"""
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'typing_update',
                'data': {
                    'user_id': event['user_id'],
                    'user_email': event['user_email'],
                    'conversation_id': event['conversation_id'],
                    'is_typing': event['is_typing']
                }
            }))
    async def message_read(self, event):
        """
        Receive and forward single message read receipt
        Only sent to message sender and conversation admins
        """
        try:
            # Check if current user should receive this read receipt
            message_sender_id = await self.get_message_sender_id(event['message_id'])
            should_receive = await self.should_receive_read_receipt(
                event['conversation_id'], 
                message_sender_id
            )
            
            # Don't send read receipt to the person who read the message
            if should_receive and event['reader_id'] != self.user.id:
                await self.send(text_data=json.dumps({
                    'type': 'message_read',
                    'data': {
                        'message_id': event['message_id'],
                        'conversation_id': event['conversation_id'],
                        'reader_id': event['reader_id'],
                        'reader_email': event['reader_email'],
                        'reader_name': event['reader_name'],
                        'read_at': event['read_at']
                    }
                }))
        except Exception as e:
            logger.error(f"Error handling message read event: {str(e)}")

    async def conversation_read(self, event):
        """
        Receive and forward conversation read receipt (multiple messages)
        Sent to all authorized users (they filter on client side)
        """
        try:
            # Check if current user should receive read receipts
            should_receive = await self.should_receive_read_receipt(event['conversation_id'])
            
            # Don't send to the person who read the messages
            if should_receive and event['reader_id'] != self.user.id:
                await self.send(text_data=json.dumps({
                    'type': 'conversation_read',
                    'data': {
                        'conversation_id': event['conversation_id'],
                        'reader_id': event['reader_id'],
                        'reader_email': event['reader_email'],
                        'reader_name': event['reader_name'],
                        'read_at': event['read_at'],
                        'messages_marked_count': event['messages_marked_count'],
                        'marked_message_ids': event['marked_message_ids']
                    }
                }))
        except Exception as e:
            logger.error(f"Error handling conversation read event: {str(e)}")
    

    
    # Database operations
    @database_sync_to_async
    def check_conversation_membership(self, conversation_id):
        """Check if user is conversation member"""
        return ConversationMembership.objects.filter(
            conversation_id=conversation_id,
            user=self.user,
            is_active=True
        ).exists()
    
    @database_sync_to_async
    def create_message(self, conversation_id, content):
        """Create new message in database"""
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            return Message.objects.create(
                conversation=conversation,
                sender=self.user,
                content=content
            )
        except Exception as e:
            logger.error(f"Error creating message: {str(e)}")
            return None
    
    @database_sync_to_async
    def serialize_message(self, message):
        """Convert message to JSON-serializable format"""
        return {
            'id': str(message.id),
            'conversation_id': str(message.conversation.id),
            'sender': {
                'id': message.sender.id,
                'email': message.sender.email,
                'full_name': f"{message.sender.first_name} {message.sender.last_name}".strip()
            },
            'content': message.content,
            'timestamp': message.timestamp.isoformat(),
            'message_type': message.message_type
        }

    # NEW: Message deletion database operations
    @database_sync_to_async
    def delete_message(self, message_id):
        """
        Perform soft delete on message with permission checks
        
        Returns:
        {
            'success': bool,
            'error': str (if failed),
            'message_id': str,
            'conversation_id': str,
            'new_content': str,
            'deleted_at': str,
            'deleted_by': int,
            'delete_reason': str
        }
        """
        try:
            with transaction.atomic():
                # Get message with related data
                message = Message.objects.select_related(
                    'conversation', 'sender'
                ).get(id=message_id)
                
                # Check if user is member of conversation
                membership = ConversationMembership.objects.filter(
                    conversation=message.conversation,
                    user=self.user,
                    is_active=True
                ).first()
                
                if not membership:
                    return {
                        'success': False,
                        'error': 'You are not a member of this conversation'
                    }
                
                # Check deletion permissions
                permission_check = self.check_delete_permission(message, membership)
                if not permission_check['can_delete']:
                    return {
                        'success': False,
                        'error': permission_check['error']
                    }
                
                # Perform soft delete
                original_content = message.content
                delete_reason = permission_check['delete_reason']
                current_time = timezone.now()
                
                # Update message to show it was deleted
                message.content = f"[Message {delete_reason}]"
                message.is_edited = True
                message.edited_at = current_time
                message.save()
                
                # Log deletion for admin purposes
                logger.info(f"Message {message_id} deleted by {self.user.email} ({delete_reason})")
                
                return {
                    'success': True,
                    'message_id': str(message.id),
                    'conversation_id': str(message.conversation.id),
                    'new_content': message.content,
                    'deleted_at': current_time.isoformat(),
                    'deleted_by': self.user.id,
                    'delete_reason': delete_reason,
                    'original_content': original_content[:50] + '...' if len(original_content) > 50 else original_content
                }
                
        except Message.DoesNotExist:
            return {
                'success': False,
                'error': 'Message not found'
            }
        except Exception as e:
            logger.error(f"Error in delete_message: {str(e)}")
            return {
                'success': False,
                'error': 'Database error occurred'
            }
    
    def check_delete_permission(self, message, membership):
        """
        Check if user has permission to delete the message
        
        Rules:
        1. Users can delete their own messages within 24 hours
        2. Conversation admins can delete any message
        3. System messages cannot be deleted
        
        Returns:
        {
            'can_delete': bool,
            'error': str (if can't delete),
            'delete_reason': str
        }
        """
        # Prevent deletion of system messages
        if message.message_type == 'system':
            return {
                'can_delete': False,
                'error': 'System messages cannot be deleted'
            }
        
        current_time = timezone.now()
        time_limit = current_time - timedelta(hours=24)
        
        # Check if user is the message sender
        if message.sender == self.user:
            if message.timestamp > time_limit:
                return {
                    'can_delete': True,
                    'delete_reason': 'deleted by author'
                }
            else:
                return {
                    'can_delete': False,
                    'error': 'You can only delete your own messages within 24 hours of posting'
                }
        
        # Check if user is conversation admin
        elif membership.role == 'admin':
            return {
                'can_delete': True,
                'delete_reason': 'deleted by admin'
            }
        
        # No permission to delete
        else:
            return {
                'can_delete': False,
                'error': 'You can only delete your own messages'
            }
    


    @database_sync_to_async
    def toggle_message_reaction(self, message_id, reaction_type):
        """
        Add, update, or remove message reaction (toggle behavior)
        
        Returns:
        {
            'success': bool,
            'error': str (if failed),
            'action': str,  # 'added', 'updated', 'removed'
            'message_id': str,
            'conversation_id': str,
            'reaction_data': dict or None,
            'user_id': int
        }
        """
        try:
            from .models import MessageReaction
            
            with transaction.atomic():
                # Get message with related data
                message = Message.objects.select_related('conversation').get(id=message_id)
                
                # Check if user is member of conversation
                membership = ConversationMembership.objects.filter(
                    conversation=message.conversation,
                    user=self.user,
                    is_active=True
                ).first()
                
                if not membership:
                    return {
                        'success': False,
                        'error': 'You are not a member of this conversation'
                    }
                
                # Get or create reaction (toggle behavior)
                reaction, created = MessageReaction.objects.get_or_create(
                    message=message,
                    user=self.user,
                    defaults={'reaction_type': reaction_type}
                )
                
                action = 'added'
                reaction_data = {
                    'user_id': self.user.id,
                    'user_email': self.user.email,
                    'user_name': f"{self.user.first_name} {self.user.last_name}".strip() or self.user.email.split('@')[0],
                    'reaction_type': reaction_type,
                    'created_at': reaction.created_at.isoformat()
                }
                
                if not created:
                    if reaction.reaction_type == reaction_type:
                        # Same reaction - remove it (toggle off)
                        reaction.delete()
                        action = 'removed'
                        reaction_data = None
                        logger.info(f"Reaction {reaction_type} removed from message {message_id} by {self.user.email}")
                    else:
                        # Different reaction - update it
                        old_reaction = reaction.reaction_type
                        reaction.reaction_type = reaction_type
                        reaction.created_at = timezone.now()  # Update timestamp for new reaction
                        reaction.save()
                        action = 'updated'
                        reaction_data['created_at'] = reaction.created_at.isoformat()
                        logger.info(f"Reaction updated from {old_reaction} to {reaction_type} on message {message_id} by {self.user.email}")
                else:
                    logger.info(f"New reaction {reaction_type} added to message {message_id} by {self.user.email}")
                
                return {
                    'success': True,
                    'action': action,
                    'message_id': str(message.id),
                    'conversation_id': str(message.conversation.id),
                    'reaction_data': reaction_data,
                    'user_id': self.user.id
                }
                
        except Message.DoesNotExist:
            return {
                'success': False,
                'error': 'Message not found'
            }
        except Exception as e:
            logger.error(f"Error toggling reaction: {str(e)}")
            return {
                'success': False,
                'error': 'Database error occurred'
            }

    @database_sync_to_async
    def mark_messages_read(self, conversation_id, message_ids=None):
        """
        Mark messages as read and update conversation membership
        
        Args:
            conversation_id: UUID of conversation
            message_ids: List of specific message UUIDs (optional)
        
        Returns:
        {
            'success': bool,
            'error': str (if failed),
            'conversation_id': str,
            'messages_marked': int,
            'read_at': str,
            'marked_message_ids': list,
            'reader_id': int,
            'reader_email': str
        }
        """
        try:
            from .models import MessageRead
            
            current_time = timezone.now()
            
            with transaction.atomic():
                # Get conversation membership
                membership = ConversationMembership.objects.filter(
                    conversation_id=conversation_id,
                    user=self.user,
                    is_active=True
                ).first()
                
                if not membership:
                    return {
                        'success': False,
                        'error': 'You are not a member of this conversation'
                    }
                
                # Determine which messages to mark as read
                if message_ids:
                    # Mark specific messages
                    messages_query = Message.objects.filter(
                        id__in=message_ids,
                        conversation_id=conversation_id
                    ).exclude(sender=self.user)  # Don't mark own messages
                else:
                    # Mark all unread messages in conversation
                    if membership.last_read_at:
                        messages_query = Message.objects.filter(
                            conversation_id=conversation_id,
                            timestamp__gt=membership.last_read_at
                        ).exclude(sender=self.user)
                    else:
                        # First time reading - mark all messages
                        messages_query = Message.objects.filter(
                            conversation_id=conversation_id
                        ).exclude(sender=self.user)
                
                # Get messages that don't already have read receipts from this user
                existing_reads = MessageRead.objects.filter(
                    message__in=messages_query,
                    user=self.user
                ).values_list('message_id', flat=True)
                
                messages_to_mark = messages_query.exclude(id__in=existing_reads)
                
                # Create read receipts for new messages
                new_reads = []
                marked_message_ids = []
                
                for message in messages_to_mark:
                    new_reads.append(
                        MessageRead(
                            message=message,
                            user=self.user,
                            read_at=current_time
                        )
                    )
                    marked_message_ids.append(str(message.id))
                
                # Bulk create read receipts
                MessageRead.objects.bulk_create(new_reads, ignore_conflicts=True)
                
                # Update conversation membership last_read_at
                membership.last_read_at = current_time
                membership.save()
                
                logger.info(f"User {self.user.email} marked {len(new_reads)} messages as read in conversation {conversation_id}")
                
                return {
                    'success': True,
                    'conversation_id': str(conversation_id),
                    'messages_marked': len(new_reads),
                    'read_at': current_time.isoformat(),
                    'marked_message_ids': marked_message_ids,
                    'reader_id': self.user.id,
                    'reader_email': self.user.email,
                    'reader_name': f"{self.user.first_name} {self.user.last_name}".strip() or self.user.email.split('@')[0]
                }
                
        except Exception as e:
            logger.error(f"Error marking messages as read: {str(e)}")
            return {
                'success': False,
                'error': 'Database error occurred'
            }

    @database_sync_to_async
    def should_receive_read_receipt(self, conversation_id, message_sender_id=None):
        """
        Check if current user should receive read receipts for this conversation
        
        Rules:
        1. Message senders always see who read their messages
        2. Conversation admins see all read receipts
        3. Regular members only see read receipts for their own messages
        
        Args:
            conversation_id: UUID of conversation
            message_sender_id: ID of message sender (for single message receipts)
        
        Returns:
            bool: True if user should receive read receipts
        """
        try:
            # Check conversation membership and role
            membership = ConversationMembership.objects.filter(
                conversation_id=conversation_id,
                user=self.user,
                is_active=True
            ).first()
            
            if not membership:
                return False
            
            # Admins always see read receipts
            if membership.role == 'admin':
                return True
            
            # For specific message, check if current user is the sender
            if message_sender_id:
                return self.user.id == message_sender_id
            
            # For conversation read events, regular members can see them
            # (they'll filter on client side for their own messages)
            return True
            
        except Exception as e:
            logger.error(f"Error checking read receipt permissions: {str(e)}")
            return False

    @database_sync_to_async
    def get_message_sender_id(self, message_id):
        """Get the sender ID of a specific message"""
        try:
            message = Message.objects.get(id=message_id)
            return message.sender.id
        except Message.DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"Error getting message sender: {str(e)}")
            return None




    @database_sync_to_async
    def set_typing_indicator(self, conversation_id):
        """Set typing indicator"""
        try:
            TypingIndicator.objects.update_or_create(
                conversation_id=conversation_id,
                user=self.user
            )
        except Exception as e:
            logger.error(f"Error setting typing indicator: {str(e)}")
    
    @database_sync_to_async
    def clear_typing_indicator(self, conversation_id):
        """Clear typing indicator for specific conversation"""
        try:
            TypingIndicator.objects.filter(
                conversation_id=conversation_id,
                user=self.user
            ).delete()
        except Exception:
            pass
    
    @database_sync_to_async
    def clear_all_typing_indicators(self):
        """Clear all typing indicators for user"""
        try:
            TypingIndicator.objects.filter(user=self.user).delete()
        except Exception:
            pass
    
    @database_sync_to_async
    def set_user_online(self):
        """Set user online status"""
        try:
            UserPresence.objects.update_or_create(
                user=self.user,
                defaults={'status': 'online', 'last_seen': timezone.now()}
            )
        except Exception as e:
            logger.error(f"Error setting user online: {str(e)}")
    
    @database_sync_to_async
    def set_user_offline(self):
        """Set user offline status"""
        try:
            UserPresence.objects.update_or_create(
                user=self.user,
                defaults={'status': 'offline', 'last_seen': timezone.now()}
            )
        except Exception:
            pass
    
    async def send_error(self, message):
        """Send error message to client"""
        await self.send(text_data=json.dumps({
            'type': 'error',
            'data': {
                'message': message,
                'timestamp': timezone.now().isoformat()
            }
        }))

    async def broadcast_reaction_update(self, reaction_data):
        """
        Broadcast reaction update to all conversation members
        """
        try:
            group_name = f"conversation_{reaction_data['conversation_id']}"
            
            await self.channel_layer.group_send(group_name, {
                'type': 'reaction_update',
                'message_id': reaction_data['message_id'],
                'conversation_id': reaction_data['conversation_id'],
                'action': reaction_data['action'],
                'reaction_data': reaction_data['reaction_data'],
                'user_id': reaction_data['user_id']
            })
            
        except Exception as e:
            logger.error(f"Error broadcasting reaction update: {str(e)}")

    async def broadcast_message_deletion(self, deletion_data):
        """
        Broadcast message deletion to all conversation members
        """
        try:
            group_name = f"conversation_{deletion_data['conversation_id']}"
            
            await self.channel_layer.group_send(group_name, {
                'type': 'message_deleted',
                'message_id': deletion_data['message_id'],
                'conversation_id': deletion_data['conversation_id'],
                'deleted_by': deletion_data['deleted_by'],
                'delete_reason': deletion_data['delete_reason'],
                'new_content': deletion_data['new_content'],
                'deleted_at': deletion_data['deleted_at']
            })
            
        except Exception as e:
            logger.error(f"Error broadcasting message deletion: {str(e)}")

    async def broadcast_read_receipts(self, read_data):
        """
        Broadcast read receipts to authorized conversation members
        Only message senders and conversation admins should see read receipts
        """
        try:
            group_name = f"conversation_{read_data['conversation_id']}"
            
            if len(read_data['marked_message_ids']) == 1:
                # Single message read receipt
                await self.channel_layer.group_send(group_name, {
                    'type': 'message_read',
                    'message_id': read_data['marked_message_ids'][0],
                    'conversation_id': read_data['conversation_id'],
                    'reader_id': read_data['reader_id'],
                    'reader_email': read_data['reader_email'],
                    'reader_name': read_data['reader_name'],
                    'read_at': read_data['read_at']
                })
            else:
                # Multiple messages read (conversation read)
                await self.channel_layer.group_send(group_name, {
                    'type': 'conversation_read',
                    'conversation_id': read_data['conversation_id'],
                    'reader_id': read_data['reader_id'],
                    'reader_email': read_data['reader_email'],
                    'reader_name': read_data['reader_name'],
                    'read_at': read_data['read_at'],
                    'messages_marked_count': read_data['messages_marked'],
                    'marked_message_ids': read_data['marked_message_ids']
                })
            
        except Exception as e:
            logger.error(f"Error broadcasting read receipts: {str(e)}")