from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('get-app-config/', views.get_app_config, name='get_app_config'),
    path('get-pods/', views.get_pods, name='get_pods'),
    path('get-pod-logs/', views.get_pod_logs, name='get_pod_logs'),
    path('summarize/', views.summarize_logs, name='summarize'),
    path('analyze/', views.analyze_logs, name='analyze'),
    path('send-rca-email/', views.send_rca_email, name='send_rca_email'),
    path('track-download/', views.track_download, name='track_download'),
    path('download-stats/', views.get_download_stats, name='download_stats'),
    path('health/', views.health_check, name='health_check'),
]