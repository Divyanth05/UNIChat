from django.contrib import admin
from .models import (
    Conversation, ConversationMembership, Message, 
    MessageReaction, UserPresence, MessageRead, TypingIndicator
)

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'type', 'university', 'created_by', 'created_at', 'is_active')
    list_filter = ('type', 'is_active', 'university', 'created_at')
    search_fields = ('name', 'description', 'created_by__email')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('created_by', 'university')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('id', 'type', 'name', 'description', 'university')
        }),
        ('Settings', {
            'fields': ('max_members', 'is_public', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

class ConversationMembershipInline(admin.TabularInline):
    model = ConversationMembership
    extra = 0
    raw_id_fields = ('user',)

@admin.register(ConversationMembership)
class ConversationMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'conversation', 'role', 'joined_at', 'is_active')
    list_filter = ('role', 'is_active', 'joined_at')
    search_fields = ('user__email', 'conversation__name')
    raw_id_fields = ('user', 'conversation')

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'conversation', 'message_type', 'timestamp', 'is_edited')
    list_filter = ('message_type', 'is_edited', 'timestamp')
    search_fields = ('content', 'sender__email', 'conversation__name')
    readonly_fields = ('id', 'timestamp', 'edited_at')
    raw_id_fields = ('sender', 'conversation', 'reply_to')
    
    fieldsets = (
        ('Message Info', {
            'fields': ('id', 'conversation', 'sender', 'content', 'message_type')
        }),
        ('File Attachment', {
            'fields': ('file_url', 'file_name', 'file_size'),
            'classes': ('collapse',)
        }),
        ('Reply Info', {
            'fields': ('reply_to',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('timestamp', 'is_edited', 'edited_at'),
            'classes': ('collapse',)
        }),
    )

@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = ('message', 'user', 'reaction_type', 'created_at')
    list_filter = ('reaction_type', 'created_at')
    search_fields = ('user__email', 'message__content')
    raw_id_fields = ('message', 'user')

@admin.register(UserPresence)
class UserPresenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'last_seen', 'status_message')
    list_filter = ('status', 'last_seen')
    search_fields = ('user__email', 'status_message')
    raw_id_fields = ('user',)

@admin.register(MessageRead)
class MessageReadAdmin(admin.ModelAdmin):
    list_display = ('message', 'user', 'read_at')
    list_filter = ('read_at',)
    search_fields = ('user__email', 'message__content')
    raw_id_fields = ('message', 'user')

@admin.register(TypingIndicator)
class TypingIndicatorAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'user', 'started_at')
    list_filter = ('started_at',)
    search_fields = ('user__email', 'conversation__name')
    raw_id_fields = ('conversation', 'user')