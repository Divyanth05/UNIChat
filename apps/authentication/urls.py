# apps/authentication/urls.py
"""
URL configuration for Smart Authentication System
Maps authentication endpoints to their corresponding views
"""

from django.urls import path
from . import views

app_name = 'authentication'

urlpatterns = [
    # Smart Authentication Flow - Step 1
    # POST /api/v1/auth/check-email/

     # Smart Authentication Flow - Step 2A  
    # POST /api/v1/auth/set-password/
    path('check-email/', views.check_email, name='check_email'),
    path('set-password/', views.set_password, name='set_password'),
     path('login/', views.login, name='login'),
    ]