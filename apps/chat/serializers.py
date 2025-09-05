from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    Conversation, ConversationMembership, Message, 
    MessageReaction, UserPresence
)

User = get_user_model()

class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user info for chat contexts """
    student_id = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'email', 'student_id', 'full_name']
    
    def get_student_id(self, obj):
        return obj.student.unique_id if hasattr(obj, 'student') and obj.student else None
    
    def get_full_name(self, obj):
        if hasattr(obj, 'student') and obj.student:
            return f"{obj.student.first_name} {obj.student.last_name}"
        return f"{obj.first_name} {obj.last_name}".strip() or obj.email.split('@')[0]


# NEW: Lightweight serializer for conversation list sidebar
class ConversationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for conversation list sidebar - only essential data"""
    display_name = serializers.SerializerMethodField()
    last_message_preview = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    other_user = serializers.SerializerMethodField()
    
    class Meta:
        model = Conversation
        fields = [
            'id', 
            'type',
            'display_name',
            'other_user',  # For personal chats - the other person's info
            'last_message_preview', 
            'unread_count',
            'updated_at'
        ]
    
    def get_display_name(self, obj):
        """Get appropriate display name based on conversation type"""
        if obj.type == 'personal':
            # For personal chats, show the other person's name
            request = self.context.get('request')
            if request and request.user:
                other_member = obj.memberships.exclude(user=request.user).filter(is_active=True).first()
                if other_member:
                    user_data = UserBasicSerializer(other_member.user).data
                    return user_data['full_name']
            return "Personal Chat"
        return obj.name or f"{obj.get_type_display()} Chat"
    
    def get_other_user(self, obj):
        """For personal chats, get the other user's basic info"""
        if obj.type == 'personal':
            request = self.context.get('request')
            if request and request.user:
                other_member = obj.memberships.exclude(user=request.user).filter(is_active=True).first()
                if other_member:
                    return UserBasicSerializer(other_member.user).data
        return None
    
    def get_last_message_preview(self, obj):
        """Get truncated preview of last message"""
        last_message = obj.messages.first()  # Already ordered by -timestamp
        if last_message:
            preview_length = 50
            content = last_message.content
            if len(content) > preview_length:
                content = content[:preview_length] + '...'
            
            return {
                'content': content,
                'sender_name': UserBasicSerializer(last_message.sender).data['full_name'],
                'timestamp': last_message.timestamp,
                'message_type': last_message.message_type
            }
        return None
    
    def get_unread_count(self, obj):
        """Calculate unread messages for current user"""
        request = self.context.get('request')
        if request and request.user:
            membership = obj.memberships.filter(user=request.user, is_active=True).first()
            if membership and membership.last_read_at:
                return obj.messages.filter(timestamp__gt=membership.last_read_at).count()
            return obj.messages.count()
        return 0


class ConversationMembershipSerializer(serializers.ModelSerializer):
    user = UserBasicSerializer(read_only=True)
    
    class Meta:
        model = ConversationMembership
        fields = [
            'user', 'role', 'joined_at', 'is_active', 
            'notifications_enabled', 'last_read_at'
        ]


class MessageReactionSerializer(serializers.ModelSerializer):
    user = UserBasicSerializer(read_only=True)
    
    class Meta:
        model = MessageReaction
        fields = ['user', 'reaction_type', 'created_at']


class MessageSerializer(serializers.ModelSerializer):
    sender = UserBasicSerializer(read_only=True)
    reactions = MessageReactionSerializer(many=True, read_only=True)
    reply_to_message = serializers.SerializerMethodField()
    
    class Meta:
        model = Message
        fields = [
            'id', 'sender', 'content', 'message_type', 'timestamp',
            'is_edited', 'edited_at', 'reply_to', 'reply_to_message',
            'file_url', 'file_name', 'file_size', 'reactions'
        ]
        read_only_fields = ['id', 'sender', 'timestamp', 'is_edited', 'edited_at']
    
    def get_reply_to_message(self, obj):
        if obj.reply_to:
            return {
                'id': str(obj.reply_to.id),
                'sender': UserBasicSerializer(obj.reply_to.sender).data,
                'content': obj.reply_to.content[:100] + ('...' if len(obj.reply_to.content) > 100 else ''),
                'timestamp': obj.reply_to.timestamp,
            }
        return None


# ORIGINAL: Detailed serializer for full conversation details (keep for detailed view)
class ConversationSerializer(serializers.ModelSerializer):
    created_by = UserBasicSerializer(read_only=True)
    members = ConversationMembershipSerializer(source='memberships', many=True, read_only=True)
    member_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    university_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Conversation
        fields = [
            'id', 'type', 'name', 'description', 'university', 'university_name',
            'display_name', 'created_by', 'created_at', 'updated_at', 'is_active',
            'max_members', 'is_public', 'members', 'member_count',
            'last_message', 'unread_count'
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']
    
    def get_member_count(self, obj):
        return obj.memberships.filter(is_active=True).count()
    
    def get_last_message(self, obj):
        last_message = obj.messages.first()
        if last_message:
            return {
                'id': str(last_message.id),
                'sender': UserBasicSerializer(last_message.sender).data,
                'content': last_message.content[:100] + ('...' if len(last_message.content) > 100 else ''),
                'timestamp': last_message.timestamp,
                'message_type': last_message.message_type
            }
        return None
    
    def get_unread_count(self, obj):
        request = self.context.get('request')
        if request and request.user:
            membership = obj.memberships.filter(user=request.user, is_active=True).first()
            if membership and membership.last_read_at:
                return obj.messages.filter(timestamp__gt=membership.last_read_at).count()
            return obj.messages.count()
        return 0
    
    def get_university_name(self, obj):
        return obj.university.name if obj.university else None
    
    def get_display_name(self, obj):
        """Get display name based on conversation type"""
        if obj.type == 'personal':
            request = self.context.get('request')
            if request and request.user:
                other_member = obj.memberships.exclude(user=request.user).filter(is_active=True).first()
                if other_member:
                    user_data = UserBasicSerializer(other_member.user).data
                    return f"Chat with {user_data['full_name']}"
            return "Personal Chat"
        return obj.name or f"{obj.get_type_display()} Chat"


class ConversationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating conversations"""
    member_emails = serializers.ListField(
        child=serializers.EmailField(),
        write_only=True,
        required=False,
        help_text="List of member emails to add to the conversation"
    )
    
    class Meta:
        model = Conversation
        fields = [
            'type', 'name', 'description', 'university', 
            'max_members', 'is_public', 'member_emails'
        ]
    
    def validate(self, attrs):
        conversation_type = attrs.get('type')
        member_emails = attrs.get('member_emails', [])
        
        if conversation_type == 'personal' and len(member_emails) != 1:
            raise serializers.ValidationError(
                "Personal conversations must have exactly one other member."
            )
        
        if conversation_type == 'group':
            if not attrs.get('name'):
                raise serializers.ValidationError(
                    "Group conversations must have a name."
                )
            if len(member_emails) == 0:
                raise serializers.ValidationError(
                    "Group conversations must have at least one member."
                )
        
        if conversation_type == 'channel':
            if not attrs.get('name'):
                raise serializers.ValidationError(
                    "Channel conversations must have a name."
                )
            if not attrs.get('university'):
                raise serializers.ValidationError(
                    "Channel conversations must be associated with a university."
                )
        
        max_members = attrs.get('max_members', 10)
        if len(member_emails) >= max_members:
            raise serializers.ValidationError(
                f"Cannot add more than {max_members - 1} members to this conversation."
            )
        
        return attrs
    
    def create(self, validated_data):
        member_emails = validated_data.pop('member_emails', [])
        request = self.context.get('request')
        
        conversation = Conversation.objects.create(
            created_by=request.user,
            **validated_data
        )
        
        ConversationMembership.objects.create(
            user=request.user,
            conversation=conversation,
            role='admin'
        )
        
        for email in member_emails:
            try:
                user = User.objects.get(email=email)
                ConversationMembership.objects.create(
                    user=user,
                    conversation=conversation,
                    role='member'
                )
            except User.DoesNotExist:
                pass
        
        return conversation


class MessageCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating messages"""
    
    class Meta:
        model = Message
        fields = [
            'conversation', 'content', 'message_type', 
            'reply_to', 'file_url', 'file_name', 'file_size'
        ]
    
    def validate_conversation(self, value):
        request = self.context.get('request')
        if not value.memberships.filter(user=request.user, is_active=True).exists():
            raise serializers.ValidationError(
                "You are not a member of this conversation."
            )
        return value
    
    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "Message content cannot be empty."
            )
        if len(value) > 2000:
            raise serializers.ValidationError(
                "Message content cannot exceed 2000 characters."
            )
        return value.strip()
    
    def create(self, validated_data):
        request = self.context.get('request')
        return Message.objects.create(
            sender=request.user,
            **validated_data
        )