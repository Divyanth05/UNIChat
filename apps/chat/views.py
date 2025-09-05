from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from .models import Conversation, ConversationMembership, Message
from django.utils import timezone
from .models import MessageReaction, MessageRead
from .serializers import (
    ConversationSerializer, MessageSerializer, 
    ConversationCreateSerializer, MessageCreateSerializer,
    ConversationListSerializer  # NEW: Import lightweight serializer
)

User = get_user_model()

# =============================================================================
# CONVERSATION MANAGEMENT ENDPOINTS
# =============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_conversations(request):
    """
    Get lightweight list of conversations user is a member of (for sidebar)
    
    GET /api/v1/conversations/
    """
    try:
        # Get conversations where user is an active member
        conversations = Conversation.objects.filter(
            memberships__user=request.user,
            memberships__is_active=True,
            is_active=True
        ).distinct().order_by('-updated_at')
        
        # Use lightweight serializer for conversation list
        serializer = ConversationListSerializer(
            conversations, 
            many=True, 
            context={'request': request}
        )
        
        return Response({
            'conversations': serializer.data,
            'count': len(serializer.data)
        })
        
    except Exception as e:
        return Response({
            'error': 'Failed to fetch conversations'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_personal_conversation(request):
    """
    Create a personal (1-on-1) conversation
    
    POST /api/v1/conversations/personal/
    {
        "member_email": "john@abc.edu"
    }
    """
    member_email = request.data.get('member_email', '').strip().lower()
    
    if not member_email:
        return Response({
            'error': 'member_email is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    if member_email == request.user.email:
        return Response({
            'error': 'Cannot create conversation with yourself'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Get the other user
        other_user = User.objects.get(email=member_email)
        
        # Check if personal conversation already exists between these users
        existing_conversation = Conversation.objects.filter(
            type='personal',
            memberships__user=request.user,
            memberships__is_active=True
        ).filter(
            memberships__user=other_user,
            memberships__is_active=True
        ).first()
        
        if existing_conversation:
            # Return lightweight version for consistency
            serializer = ConversationListSerializer(
                existing_conversation, 
                context={'request': request}
            )
            return Response({
                'conversation': serializer.data,
                'message': 'Conversation already exists'
            })
        
        # Create new personal conversation
        with transaction.atomic():
            conversation = Conversation.objects.create(
                type='personal',
                created_by=request.user,
                max_members=2
            )
            
            # Add both users as members
            ConversationMembership.objects.create(
                user=request.user,
                conversation=conversation,
                role='member'
            )
            
            ConversationMembership.objects.create(
                user=other_user,
                conversation=conversation,
                role='member'
            )
        
        # Return lightweight version
        serializer = ConversationListSerializer(
            conversation, 
            context={'request': request}
        )
        
        return Response({
            'conversation': serializer.data,
            'message': 'Personal conversation created successfully'
        }, status=status.HTTP_201_CREATED)
        
    except User.DoesNotExist:
        return Response({
            'error': 'User with this email does not exist'
        }, status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        return Response({
            'error': 'Failed to create conversation'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_group_conversation(request):
    """
    Create a group conversation
    
    POST /api/v1/conversations/groups/
    {
        "name": "CS101 Study Group",
        "description": "Weekly study sessions",
        "member_emails": ["john@abc.edu", "sarah@abc.edu"],
        "max_members": 10
    }
    """
    serializer = ConversationCreateSerializer(
        data=request.data, 
        context={'request': request}
    )
    
    if serializer.is_valid():
        try:
            conversation = serializer.save()
            
            # Return lightweight version for consistency
            response_serializer = ConversationListSerializer(
                conversation, 
                context={'request': request}
            )
            
            return Response({
                'conversation': response_serializer.data,
                'message': f'Group "{conversation.name}" created successfully'
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({
                'error': 'Failed to create group conversation'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_conversation_detail(request, conversation_id):
    """
    Get FULL conversation details with members and recent messages
    
    GET /api/v1/conversations/{conversation_id}/
    
    Use this when user clicks on a conversation - returns detailed data
    """
    try:
        # Get conversation and verify membership
        conversation = get_object_or_404(Conversation, id=conversation_id)
        
        # Check if user is a member
        membership = ConversationMembership.objects.filter(
            conversation=conversation,
            user=request.user,
            is_active=True
        ).first()
        
        if not membership:
            return Response({
                'error': 'You are not a member of this conversation'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Use FULL detailed serializer when getting specific conversation
        serializer = ConversationSerializer(
            conversation, 
            context={'request': request}
        )
        
        return Response({
            'conversation': serializer.data
        })
        
    except Exception as e:
        return Response({
            'error': 'Failed to fetch conversation details'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# =============================================================================
# MESSAGE MANAGEMENT ENDPOINTS
# =============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_conversation_messages(request, conversation_id):
    """
    Get message history for a conversation
    
    GET /api/v1/conversations/{conversation_id}/messages/
    Query params: ?page=1&limit=50
    """
    try:
        # Verify user is member of conversation
        membership = ConversationMembership.objects.filter(
            conversation_id=conversation_id,
            user=request.user,
            is_active=True
        ).first()
        
        if not membership:
            return Response({
                'error': 'You are not a member of this conversation'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Get messages with pagination
        limit = min(int(request.GET.get('limit', 50)), 100)  # Max 100 messages
        page = int(request.GET.get('page', 1))
        offset = (page - 1) * limit
        
        messages = Message.objects.filter(
            conversation_id=conversation_id
        ).select_related('sender').order_by('-timestamp')[offset:offset + limit]
        
        serializer = MessageSerializer(messages, many=True)
        
        # Reverse to show oldest first
        messages_data = list(reversed(serializer.data))
        
        return Response({
            'messages': messages_data,
            'count': len(messages_data),
            'page': page,
            'has_more': len(messages) == limit
        })
        
    except ValueError:
        return Response({
            'error': 'Invalid page or limit parameter'
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        return Response({
            'error': 'Failed to fetch messages'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_message_rest(request, conversation_id):
    """
    Send message via REST API (backup to WebSocket)
    
    POST /api/v1/conversations/{conversation_id}/messages/
    {
        "content": "Hello everyone!",
        "message_type": "text",
        "reply_to": "uuid-of-message-being-replied-to"
    }
    """
    # Verify membership
    membership = ConversationMembership.objects.filter(
        conversation_id=conversation_id,
        user=request.user,
        is_active=True
    ).first()
    
    if not membership:
        return Response({
            'error': 'You are not a member of this conversation'
        }, status=status.HTTP_403_FORBIDDEN)
    
    # Prepare data
    message_data = request.data.copy()
    message_data['conversation'] = conversation_id
    
    serializer = MessageCreateSerializer(
        data=message_data, 
        context={'request': request}
    )
    
    if serializer.is_valid():
        try:
            message = serializer.save()
            response_serializer = MessageSerializer(message)
            
            return Response({
                'message': response_serializer.data,
                'status': 'Message sent successfully'
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({
                'error': 'Failed to send message'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# =============================================================================
# CONVERSATION MEMBERSHIP ENDPOINTS
# =============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_conversation_member(request, conversation_id):
    """
    Add member to group conversation (admin only)
    
    POST /api/v1/conversations/{conversation_id}/members/
    {
        "member_email": "newuser@abc.edu"
    }
    """
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id)
        
        # Check if user is admin of this conversation
        user_membership = ConversationMembership.objects.filter(
            conversation=conversation,
            user=request.user,
            role='admin',
            is_active=True
        ).first()
        
        if not user_membership:
            return Response({
                'error': 'Only admins can add members to this conversation'
            }, status=status.HTTP_403_FORBIDDEN)
        
        if conversation.type == 'personal':
            return Response({
                'error': 'Cannot add members to personal conversations'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        member_email = request.data.get('member_email', '').strip().lower()
        if not member_email:
            return Response({
                'error': 'member_email is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get new member
        new_member = User.objects.get(email=member_email)
        
        # Check if already a member
        existing_membership = ConversationMembership.objects.filter(
            conversation=conversation,
            user=new_member
        ).first()
        
        if existing_membership and existing_membership.is_active:
            return Response({
                'error': 'User is already a member of this conversation'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check member limit
        current_member_count = ConversationMembership.objects.filter(
            conversation=conversation,
            is_active=True
        ).count()
        
        if current_member_count >= conversation.max_members:
            return Response({
                'error': f'Conversation has reached maximum members ({conversation.max_members})'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Add or reactivate membership
        if existing_membership:
            existing_membership.is_active = True
            existing_membership.role = 'member'
            existing_membership.save()
        else:
            ConversationMembership.objects.create(
                conversation=conversation,
                user=new_member,
                role='member'
            )
        
        return Response({
            'message': f'{new_member.email} has been added to the conversation'
        }, status=status.HTTP_201_CREATED)
        
    except User.DoesNotExist:
        return Response({
            'error': 'User with this email does not exist'
        }, status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        return Response({
            'error': 'Failed to add member'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def leave_conversation(request, conversation_id):
    """
    Leave a conversation
    
    DELETE /api/v1/conversations/{conversation_id}/leave/
    """
    try:
        membership = ConversationMembership.objects.filter(
            conversation_id=conversation_id,
            user=request.user,
            is_active=True
        ).first()
        
        if not membership:
            return Response({
                'error': 'You are not a member of this conversation'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Deactivate membership (soft delete)
        membership.is_active = False
        membership.save()
        
        return Response({
            'message': 'You have left the conversation'
        })
        
    except Exception as e:
        return Response({
            'error': 'Failed to leave conversation'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# =============================================================================
# UNIVERSITY CHANNELS ENDPOINT
# =============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_university_channels(request):
    """
    Get public channels for user's university
    
    GET /api/v1/conversations/channels/
    """
    try:
        if not hasattr(request.user, 'student') or not request.user.student:
            return Response({
                'channels': [],
                'message': 'Only students can access university channels'
            })
        
        user_university = request.user.student.university
        
        channels = Conversation.objects.filter(
            type='channel',
            university=user_university,
            is_public=True,
            is_active=True
        ).order_by('name')
        
        # Use lightweight serializer for channel list too
        serializer = ConversationListSerializer(
            channels, 
            many=True, 
            context={'request': request}
        )
        
        return Response({
            'channels': serializer.data,
            'university': user_university.name,
            'count': len(serializer.data)
        })
        
    except Exception as e:
        return Response({
            'error': 'Failed to fetch university channels'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)