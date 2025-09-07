from django.urls import path
from . import views

app_name = 'files'

urlpatterns = [
    # File upload endpoint
    path('upload/', views.upload_file, name='upload_file'),
    
    # File download endpoint  
    path('<uuid:file_id>/download/', views.download_file, name='download_file'),
    
    # Upload status endpoint (NEW)
    path('upload-status/<str:task_id>/', views.upload_status, name='upload_status'),
]