from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('summarize/', views.summarize_logs, name='summarize_logs'),
    path('analyze/', views.analyze_logs, name='analyze_logs'),
    path('get-app-config/', views.get_app_config, name='get_app_config'),  # serves app_config.json
]
