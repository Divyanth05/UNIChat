from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    # Conversation management
    path('conversations/', views.list_conversations, name='list_conversations'),
    path('conversations/personal/', views.create_personal_conversation, name='create_personal_conversation'),
    path('conversations/groups/', views.create_group_conversation, name='create_group_conversation'),
    path('conversations/channels/', views.list_university_channels, name='list_university_channels'),
    path('conversations/<uuid:conversation_id>/', views.get_conversation_detail, name='get_conversation_detail'),
    
    # Message management
    path('conversations/<uuid:conversation_id>/messages/', views.get_conversation_messages, name='get_conversation_messages'),
    path('conversations/<uuid:conversation_id>/messages/send/', views.send_message_rest, name='send_message_rest'),
    
    # Membership management
    path('conversations/<uuid:conversation_id>/members/', views.add_conversation_member, name='add_conversation_member'),
    path('conversations/<uuid:conversation_id>/leave/', views.leave_conversation, name='leave_conversation'),
]