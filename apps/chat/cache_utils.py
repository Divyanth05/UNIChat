from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

class AuthCacheManager:
    """Utility class to manage authentication-related Redis caching"""
    
    @staticmethod
    def get_user_cache_key(user_id):
        """Get cache key for user authentication data"""
        return f"auth_user:{user_id}"
    
    @staticmethod
    def cache_user(user, timeout=1800):
        """
        Cache user authentication data
        
        Args:
            user: User object to cache
            timeout: Cache timeout in seconds (default: 30 minutes)
        """
        cache_key = AuthCacheManager.get_user_cache_key(user.id)
        
        user_data = {
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_active': user.is_active,
            'is_staff': user.is_staff,
        }
        
        # Include student data if available
        if hasattr(user, 'student') and user.student:
            user_data['student'] = {
                'unique_id': user.student.unique_id,
                'first_name': user.student.first_name,
                'last_name': user.student.last_name,
                'university_id': user.student.university_id,
            }
        
        cache.set(cache_key, user_data, timeout=timeout)
        logger.info(f"User {user.id} cached for {timeout} seconds")
    
    @staticmethod
    def invalidate_user_cache(user_id):
        """
        Remove user from authentication cache
        
        Args:
            user_id: ID of user to remove from cache
        """
        cache_key = AuthCacheManager.get_user_cache_key(user_id)
        cache.delete(cache_key)
        logger.info(f"User {user_id} cache invalidated")
    
    @staticmethod
    def get_cached_user(user_id):
        """
        Get cached user data
        
        Args:
            user_id: ID of user to retrieve
            
        Returns:
            User data dict or None if not cached
        """
        cache_key = AuthCacheManager.get_user_cache_key(user_id)
        return cache.get(cache_key)
    
    @staticmethod
    def refresh_user_cache(user):
        """
        Refresh user cache with latest data
        
        Args:
            user: User object with latest data
        """
        AuthCacheManager.invalidate_user_cache(user.id)
        AuthCacheManager.cache_user(user)
        logger.info(f"User {user.id} cache refreshed")

# Utility functions for easy import
def cache_user_auth(user, timeout=1800):
    """Cache user authentication data"""
    AuthCacheManager.cache_user(user, timeout)

def invalidate_user_auth_cache(user_id):
    """Invalidate user authentication cache"""
    AuthCacheManager.invalidate_user_cache(user_id)

def refresh_user_auth_cache(user):
    """Refresh user authentication cache"""
    AuthCacheManager.refresh_user_cache(user)