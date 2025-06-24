import os
import json
import re
import requests
import logging
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.core.cache import cache

# Set up logging
logger = logging.getLogger(__name__)

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")

# Cache timeout for app config (5 minutes)
APP_CONFIG_CACHE_TIMEOUT = 300


def sanitize_filename(value):
    """Sanitize filename to avoid path traversal and invalid characters."""
    if not value:
        return "unknown"
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', str(value))


@csrf_exempt
def index(request):
    context = {}

    if request.method == 'POST':
        app = request.POST.get('application', '').strip()
        cluster = request.POST.get('cluster', '').strip()
        bundle = request.POST.get('testtype', '').strip()

        # Validate required fields
        if not all([app, cluster, bundle]):
            context['log'] = "❌ Error: Please select Application, Cluster, and Bundle before running the test."
            return render(request, 'app/index.html', context)

        # Generate log file name with timestamp for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_name = f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-{timestamp}.log"
        log_file_path = os.path.join("app", "static", "logs", log_file_name)

        # Try to find existing log file (without timestamp)
        simple_log_name = f"{sanitize_filename(app)}-{sanitize_filename(bundle)}.log"
        simple_log_path = os.path.join("app", "static", "logs", simple_log_name)

        try:
            if os.path.exists(simple_log_path):
                with open(simple_log_path, "r", encoding='utf-8') as f:
                    log_content = f.read()
                context['log'] = log_content
                context['log_file_name'] = simple_log_name
                logger.info(f"Successfully loaded log file: {simple_log_path}")
            elif os.path.exists(log_file_path):
                with open(log_file_path, "r", encoding='utf-8') as f:
                    log_content = f.read()
                context['log'] = log_content
                context['log_file_name'] = log_file_name
                logger.info(f"Successfully loaded log file: {log_file_path}")
            else:
                # In a real scenario, you might trigger log generation here
                context['log'] = f"⏳ Generating logs for {app} - {bundle} on {cluster}...\n\nThis might take a few moments. Please wait."
                context['log_file_name'] = None
                logger.warning(f"Log file not found: {simple_log_path}")
                
                # You could add actual log generation logic here
                # generate_logs(app, cluster, bundle, log_file_path)
                
        except UnicodeDecodeError:
            context['log'] = "❌ Error: Log file contains invalid characters. Please check the file encoding."
            context['log_file_name'] = None
            logger.error(f"Unicode decode error for log file: {simple_log_path}")
        except Exception as e:
            context['log'] = f"❌ Error reading log file: {str(e)}"
            context['log_file_name'] = None
            logger.error(f"Error reading log file {simple_log_path}: {str(e)}")

        context.update({
            "selected_app": app,
            "selected_cluster": cluster,
            "selected_test": bundle,
        })

    return render(request, 'app/index.html', context)


@require_POST
@csrf_exempt
def summarize_logs(request):
    log_text = request.POST.get("log_text", "").strip()
    
    if not log_text or log_text == "Waiting for execution...":
        return JsonResponse({"summary": "❌ No logs available to summarize."})

    # Check for log text length (Together API has limits)
    if len(log_text) > 8000:  # Truncate very long logs
        log_text = log_text[:8000] + "\n... (truncated for analysis)"
        logger.warning("Log text truncated for summarization due to length")

    try:
        headers = {
            "Authorization": f"Bearer {TOGETHER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "meta-llama/Llama-3-8b-chat-hf",
            "messages": [
                {
                    "role": "system",
                    "content": """You are an expert DevOps engineer who specializes in analyzing application logs. 
                    Provide a clear, concise summary that includes:
                    1. Overall status (Success/Failure/Warning)
                    2. Key events or operations performed
                    3. Any errors or warnings found
                    4. Performance metrics if available
                    Keep the summary under 200 words and use bullet points for clarity."""
                },
                {
                    "role": "user",
                    "content": f"Analyze and summarize this application log:\n\n{log_text}"
                }
            ],
            "max_tokens": 400,
            "temperature": 0.3,  # Lower temperature for more consistent analysis
            "top_p": 0.9
        }
        
        response = requests.post(
            "https://api.together.xyz/v1/chat/completions", 
            headers=headers, 
            json=data,
            timeout=30  # 30 second timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            summary = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if summary:
                logger.info("Successfully generated log summary")
                return JsonResponse({"summary": summary.strip()})
            else:
                logger.error("Empty response from Together API")
                return JsonResponse({"summary": "❌ Received empty response from AI service."})
        else:
            error_msg = f"API error {response.status_code}: {response.text[:200]}"
            logger.error(f"Together API error: {error_msg}")
            return JsonResponse({"summary": f"❌ {error_msg}"})
            
    except requests.exceptions.Timeout:
        logger.error("Timeout while calling Together API for summarization")
        return JsonResponse({"summary": "❌ Request timed out. Please try again."})
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during summarization: {str(e)}")
        return JsonResponse({"summary": f"❌ Network error: {str(e)}"})
    except Exception as e:
        logger.error(f"Unexpected error during summarization: {str(e)}")
        return JsonResponse({"summary": f"❌ Unexpected error: {str(e)}"})


@require_POST
@csrf_exempt
def analyze_logs(request):
    log_text = request.POST.get("log_text", "").strip()
    
    if not log_text or log_text == "Waiting for execution...":
        return JsonResponse({"analysis": "❌ No logs available for root cause analysis."})

    # Check for log text length
    if len(log_text) > 8000:
        log_text = log_text[:8000] + "\n... (truncated for analysis)"
        logger.warning("Log text truncated for RCA due to length")

    try:
        headers = {
            "Authorization": f"Bearer {TOGETHER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Using chat completions for better RCA analysis
        data = {
            "model": "meta-llama/Llama-3-8b-chat-hf",
            "messages": [
                {
                    "role": "system",
                    "content": """You are a senior DevOps engineer specializing in root cause analysis. 
                    Analyze the provided logs and identify:
                    1. Primary errors or failures
                    2. Root cause of any issues
                    3. Impact assessment
                    4. Recommended actions to resolve
                    5. Prevention strategies
                    
                    Be specific and actionable in your recommendations. If no errors are found, indicate successful execution."""
                },
                {
                    "role": "user",
                    "content": f"Perform root cause analysis on this log:\n\n{log_text}"
                }
            ],
            "max_tokens": 600,
            "temperature": 0.2,  # Very low temperature for analytical consistency
            "top_p": 0.8
        }
        
        response = requests.post(
            "https://api.together.xyz/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            analysis = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if analysis:
                logger.info("Successfully generated RCA analysis")
                return JsonResponse({"analysis": analysis.strip()})
            else:
                logger.error("Empty response from Together API for RCA")
                return JsonResponse({"analysis": "❌ Received empty response from AI service."})
        else:
            error_msg = f"API error {response.status_code}: {response.text[:200]}"
            logger.error(f"Together API error during RCA: {error_msg}")
            return JsonResponse({"analysis": f"❌ {error_msg}"})
            
    except requests.exceptions.Timeout:
        logger.error("Timeout while calling Together API for RCA")
        return JsonResponse({"analysis": "❌ Request timed out. Please try again."})
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during RCA: {str(e)}")
        return JsonResponse({"analysis": f"❌ Network error: {str(e)}"})
    except Exception as e:
        logger.error(f"Unexpected error during RCA: {str(e)}")
        return JsonResponse({"analysis": f"❌ Unexpected error: {str(e)}"})


@csrf_exempt
def get_app_config(request):
    """Serve app configuration with caching for better performance"""
    
    # Try to get from cache first
    cached_config = cache.get('app_config')
    if cached_config:
        logger.debug("Returning cached app configuration")
        return JsonResponse(cached_config)
    
    config_path = os.path.join("app", "static", "app_config.json")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Validate configuration structure
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a dictionary")
        
        # Cache the configuration
        cache.set('app_config', data, APP_CONFIG_CACHE_TIMEOUT)
        logger.info("Successfully loaded and cached app configuration")
        
        return JsonResponse(data)
        
    except FileNotFoundError:
        logger.error(f"App configuration file not found: {config_path}")
        return JsonResponse({
            "error": "Configuration file not found",
            "message": "Please ensure app_config.json exists in the static directory"
        }, status=404)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in app configuration: {str(e)}")
        return JsonResponse({
            "error": "Invalid configuration format",
            "message": f"JSON decode error: {str(e)}"
        }, status=400)
    except Exception as e:
        logger.error(f"Error loading app configuration: {str(e)}")
        return JsonResponse({
            "error": "Failed to load configuration",
            "message": str(e)
        }, status=500)


# Optional: Add a health check endpoint
@csrf_exempt
def health_check(request):
    """Simple health check endpoint"""
    try:
        # Check if Together API key is configured
        api_configured = bool(TOGETHER_API_KEY)
        
        # Check if app config exists
        config_path = os.path.join("app", "static", "app_config.json")
        config_exists = os.path.exists(config_path)
        
        # Check logs directory
        logs_dir = os.path.join("app", "static", "logs")
        logs_dir_exists = os.path.exists(logs_dir)
        
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "checks": {
                "together_api_configured": api_configured,
                "app_config_exists": config_exists,
                "logs_directory_exists": logs_dir_exists
            }
        }
        
        # Determine overall health
        if not all([api_configured, config_exists, logs_dir_exists]):
            status["status"] = "degraded"
        
        return JsonResponse(status)
        
    except Exception as e:
        return JsonResponse({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }, status=500)