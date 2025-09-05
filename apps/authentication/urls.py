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
    path('check-email/', views.check_email, name='check_email'),
     # Smart Authentication Flow - Step 2A  
    # POST /api/v1/auth/set-password/
    path('set-password/', views.set_password, name='set_password'),
    # Smart Authentication Flow - Step 2B
    # POST /api/v1/auth/login/
    path('login/', views.login, name='login'),
    # ADMIN ENDPOINTS
    # POST /api/v1/auth/admin/add-university/
    path('admin/add-university/', views.add_university, name='add_university'),
    path('admin/upload-students/', views.upload_students, name='upload_students'), 
    path('admin/universities/', views.list_universities, name='list_universities'),
    path('logout/', views.logout, name='logout'),
   
    ]   