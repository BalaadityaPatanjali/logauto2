from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('summarize/', views.summarize_logs, name='summarize_logs'),
    path('analyze/', views.analyze_logs, name='analyze_logs'),
    path('get-app-config/', views.get_app_config, name='get_app_config'),  # serves app_config.json
    path('get-pods/', views.get_pods, name='get_pods'),  # NEW: Get pods for selected bundle
    path('get-pod-logs/', views.get_pod_logs, name='get_pod_logs'),  # NEW: Get logs for selected pod
    path('health/', views.health_check, name='health_check'),  # Optional: Health check endpoint
]