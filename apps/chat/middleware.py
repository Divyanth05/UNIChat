import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from urllib.parse import parse_qs
import logging

# Import the cache utilities
from apps.chat.cache_utils import AuthCacheManager, cache_user_auth

User = get_user_model()
logger = logging.getLogger(__name__)

@database_sync_to_async
def get_user_from_token_with_cache(token):
    """Get user from JWT token with Redis caching"""
    try:
        # Decode JWT token
        payload = jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=['HS256']
        )
        
        user_id = payload.get('user_id')
        if not user_id:
            return AnonymousUser()
        
        # Try to get user from Redis cache first using utils
        cached_user_data = AuthCacheManager.get_cached_user(user_id)
        
        if cached_user_data:
            logger.info(f"User {user_id} found in cache")
            # Reconstruct user object from cached data
            user = User(
                id=cached_user_data['id'],
                email=cached_user_data['email'],
                first_name=cached_user_data.get('first_name', ''),
                last_name=cached_user_data.get('last_name', ''),
                is_active=cached_user_data.get('is_active', True),
                is_staff=cached_user_data.get('is_staff', False),
            )
            # Set the _state to indicate this is from database
            user._state.adding = False
            user._state.db = 'default'
            return user
        
        # Cache miss - get from database
        logger.info(f"User {user_id} not in cache, fetching from database")
        try:
            user = User.objects.select_related('student').get(id=user_id)
            
            # Cache user data using utils
            cache_user_auth(user, timeout=1800)  # Cache for 30 minutes
            
            return user
            
        except User.DoesNotExist:
            logger.warning(f"User {user_id} not found in database")
            return AnonymousUser()
        
    except jwt.ExpiredSignatureError:
        logger.warning("WebSocket JWT token expired")
    except jwt.InvalidTokenError:
        logger.warning("WebSocket JWT token invalid")
    except Exception as e:
        logger.error(f"WebSocket JWT authentication error: {str(e)}")
    
    return AnonymousUser()

class JWTAuthMiddleware(BaseMiddleware):
    """JWT authentication middleware for WebSocket connections with Redis caching"""
    
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            query_string = scope.get('query_string', b'').decode()
            query_params = parse_qs(query_string)
            
            token = query_params.get('token', [None])[0]
            
            if token:
                scope['user'] = await get_user_from_token_with_cache(token)
            else:
                scope['user'] = AnonymousUser()
        
        return await super().__call__(scope, receive, send)

def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)