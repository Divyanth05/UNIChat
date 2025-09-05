from django.db import models
from django.contrib.auth import get_user_model
from apps.authentication.models import University
import uuid

User = get_user_model()

class Conversation(models.Model):
    """
    Represents different types of conversations: personal, group, or public channels
    """
    CONVERSATION_TYPES = [
        ('personal', 'Personal'),
        ('group', 'Group'),
        ('channel', 'Channel'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=20, choices=CONVERSATION_TYPES)
    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    
    # For university-specific channels
    university = models.ForeignKey(
        University, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        help_text="University context for channels"
    )
    
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='created_conversations'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    # Chat settings
    max_members = models.IntegerField(default=10)  # For group chats
    is_public = models.BooleanField(default=False)  # For channels
    
    class Meta:
        indexes = [
            models.Index(fields=['type', 'university']),
            models.Index(fields=['created_at']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        if self.type == 'personal':
            members = self.memberships.all()[:2]
            if len(members) >= 2:
                return f"Chat: {members[0].user.email} & {members[1].user.email}"
            return f"Personal Chat {self.id}"
        return self.name or f"{self.get_type_display()} {self.id}"

class ConversationMembership(models.Model):
    """
    Manages user membership and roles in conversations
    Only Admin and Member roles - simplified structure
    """
    ROLES = [
        ('member', 'Member'),
        ('admin', 'Admin'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    conversation = models.ForeignKey(
        Conversation, 
        on_delete=models.CASCADE, 
        related_name='memberships'
    )
    role = models.CharField(max_length=20, choices=ROLES, default='member')
    joined_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    # Notification settings
    notifications_enabled = models.BooleanField(default=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ('user', 'conversation')
        indexes = [
            models.Index(fields=['conversation', 'is_active']),
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['role']),
        ]
    
    def __str__(self):
        role_display = "👑" if self.role == 'admin' else "👤"
        return f"{role_display} {self.user.email} in {self.conversation}"

class Message(models.Model):
    """
    Individual messages within conversations
    """
    MESSAGE_TYPES = [
        ('text', 'Text'),
        ('file', 'File'),
        ('image', 'Image'),
        ('system', 'System'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, 
        on_delete=models.CASCADE, 
        related_name='messages'
    )
    sender = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='sent_messages'
    )
    
    content = models.TextField()
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPES, default='text')
    timestamp = models.DateTimeField(auto_now_add=True)
    
    # Message editing
    is_edited = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)
    
    # Reply functionality
    reply_to = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='replies'
    )
    
    # File attachment (if message_type is 'file' or 'image')
    file_url = models.URLField(blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)  # in bytes
    
    class Meta:
        indexes = [
            models.Index(fields=['conversation', 'timestamp']),
            models.Index(fields=['sender', 'timestamp']),
            models.Index(fields=['message_type']),
        ]
        ordering = ['-timestamp']
    
    def __str__(self):
        content_preview = self.content[:50] + '...' if len(self.content) > 50 else self.content
        return f"{self.sender.email}: {content_preview}"

class MessageReaction(models.Model):
    """
    Emoji reactions to messages
    """
    REACTION_TYPES = [
        ('👍', 'Thumbs Up'),
        ('❤️', 'Heart'),
        ('😂', 'Laugh'),
        ('😮', 'Wow'),
        ('😢', 'Sad'),
        ('😡', 'Angry'),
        ('👎', 'Thumbs Down'),
        ('🔥', 'Fire'),
        ('💯', 'Hundred'),
        ('👏', 'Clap'),
    ]
    
    message = models.ForeignKey(
        Message, 
        on_delete=models.CASCADE, 
        related_name='reactions'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    reaction_type = models.CharField(max_length=10, choices=REACTION_TYPES)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('message', 'user', 'reaction_type')
        indexes = [
            models.Index(fields=['message']),
        ]
    
    def __str__(self):
        return f"{self.user.email} reacted {self.reaction_type} to message"

class UserPresence(models.Model):
    """
    Track user online/offline status
    """
    STATUS_CHOICES = [
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('away', 'Away'),
        ('busy', 'Busy'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='presence')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='offline')
    last_seen = models.DateTimeField(auto_now=True)
    status_message = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return f"{self.user.email} - {self.status}"

class MessageRead(models.Model):
    """
    Track which messages have been read by which users
    """
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='read_by')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    read_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('message', 'user')
        indexes = [
            models.Index(fields=['message']),
            models.Index(fields=['user', 'read_at']),
        ]
    
    def __str__(self):
        return f"{self.user.email} read message at {self.read_at}"

class TypingIndicator(models.Model):
    """
    Temporary model to track who is currently typing
    """
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('conversation', 'user')
        indexes = [
            models.Index(fields=['conversation']),
            models.Index(fields=['started_at']),  # For cleanup of old typing indicators
        ]
    
    def __str__(self):
        return f"{self.user.email} typing in {self.conversation}"