"""
ASGI config for UNI_Chat_backend project.
"""

import os
import django
from django.core.asgi import get_asgi_application

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'UNI_Chat_backend.settings')
django.setup()

# Import after Django is set up
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from apps.chat.middleware import JWTAuthMiddlewareStack
from apps.chat.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddlewareStack(
            URLRouter(
                websocket_urlpatterns
            )
        )
    ),
})