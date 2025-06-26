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
# Cache timeout for pods (2 minutes)
PODS_CACHE_TIMEOUT = 120


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
def get_pods(request):
    """Get available pods for a specific application, cluster, and bundle"""
    try:
        app = request.POST.get('application', '').strip()
        cluster = request.POST.get('cluster', '').strip()
        bundle = request.POST.get('bundle', '').strip()

        # Validate required fields
        if not all([app, cluster, bundle]):
            return JsonResponse({
                "error": "Missing required parameters",
                "pods": []
            }, status=400)

        # Create cache key for this specific combination
        cache_key = f"pods_{sanitize_filename(app)}_{sanitize_filename(cluster)}_{sanitize_filename(bundle)}"
        
        # Try to get from cache first
        cached_pods = cache.get(cache_key)
        if cached_pods:
            logger.debug(f"Returning cached pods for {app}-{cluster}-{bundle}")
            return JsonResponse({"pods": cached_pods})

        # Try to load pods from configuration file first
        pods_config_path = os.path.join("app", "static", "pods_config.json")
        pods = []

        if os.path.exists(pods_config_path):
            try:
                with open(pods_config_path, 'r', encoding='utf-8') as f:
                    pods_config = json.load(f)
                
                # Navigate through the configuration hierarchy
                app_config = pods_config.get(app, {})
                cluster_config = app_config.get(cluster, {})
                bundle_pods = cluster_config.get(bundle, [])
                
                if isinstance(bundle_pods, list):
                    pods = bundle_pods
                else:
                    pods = []
                    
                logger.info(f"Loaded {len(pods)} pods from config for {app}-{cluster}-{bundle}")
                
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error reading pods config: {str(e)}")
                
        # If no pods from config, generate sample pods (for demo purposes)
        if not pods:
            pods = generate_sample_pods(app, bundle)
            logger.info(f"Generated {len(pods)} sample pods for {app}-{cluster}-{bundle}")

        # Cache the pods for future requests
        cache.set(cache_key, pods, PODS_CACHE_TIMEOUT)
        
        return JsonResponse({"pods": pods})

    except Exception as e:
        logger.error(f"Error getting pods: {str(e)}")
        return JsonResponse({
            "error": f"Failed to get pods: {str(e)}",
            "pods": []
        }, status=500)


def generate_sample_pods(app, bundle):
    """Generate sample pods for demo purposes"""
    # This is a fallback function that generates sample pod names
    # In a real implementation, you would fetch this from Kubernetes API
    
    base_pods = [
        f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-web-001",
        f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-web-002", 
        f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-api-001",
        f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-worker-001",
        f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-error-pod",
        f"{sanitize_filename(app)}-{sanitize_filename(bundle)}-warn-service"
    ]
    
    # Return pods as a list of dictionaries with name and display_name
    return [
        {
            "name": pod,
            "display_name": pod.replace('_', '-').replace('-error-pod', ' (Error Demo)').replace('-warn-service', ' (Warning Demo)')
        }
        for pod in base_pods
    ]


@require_POST
@csrf_exempt
def get_pod_logs(request):
    """Get logs for a specific pod"""
    try:
        app = request.POST.get('application', '').strip()
        cluster = request.POST.get('cluster', '').strip()
        bundle = request.POST.get('bundle', '').strip()
        pod = request.POST.get('pod', '').strip()

        # Validate required fields
        if not all([app, cluster, bundle, pod]):
            return JsonResponse({
                "error": "Missing required parameters",
                "logs": ""
            }, status=400)

        # Sanitize inputs
        safe_app = sanitize_filename(app)
        safe_cluster = sanitize_filename(cluster)
        safe_bundle = sanitize_filename(bundle)
        safe_pod = sanitize_filename(pod)

        # Create cache key for this specific pod logs
        cache_key = f"pod_logs_{safe_app}_{safe_cluster}_{safe_bundle}_{safe_pod}"
        
        # Try to get from cache first (shorter cache time for logs)
        cached_logs = cache.get(cache_key)
        if cached_logs:
            logger.debug(f"Returning cached logs for pod {pod}")
            return JsonResponse({"logs": cached_logs})

        # Try to find pod-specific log file
        log_patterns = [
            f"{safe_app}-{safe_bundle}-{safe_pod}.log",
            f"{safe_pod}.log",
            f"{safe_app}-{safe_pod}.log",
            f"{safe_cluster}-{safe_bundle}-{safe_pod}.log"
        ]

        logs_dir = os.path.join("app", "static", "logs")
        log_content = None

        # Try each pattern to find the log file
        for pattern in log_patterns:
            log_path = os.path.join(logs_dir, pattern)
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding='utf-8') as f:
                        log_content = f.read()
                    logger.info(f"Successfully loaded pod log file: {log_path}")
                    break
                except UnicodeDecodeError:
                    logger.error(f"Unicode decode error for pod log file: {log_path}")
                    continue
                except Exception as e:
                    logger.error(f"Error reading pod log file {log_path}: {str(e)}")
                    continue

        # If no specific pod log found, generate sample log content
        if log_content is None:
            log_content = generate_sample_pod_logs(app, bundle, pod)
            logger.info(f"Generated sample logs for pod {pod}")

        # Cache the logs for a short time (30 seconds)
        cache.set(cache_key, log_content, 30)
        
        return JsonResponse({"logs": log_content})

    except Exception as e:
        logger.error(f"Error getting pod logs: {str(e)}")
        return JsonResponse({
            "error": f"Failed to get pod logs: {str(e)}",
            "logs": ""
        }, status=500)


def generate_sample_pod_logs(app, bundle, pod):
    """Generate sample log content for demo purposes with both success and failure scenarios"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Generate different log scenarios based on pod name patterns
    if "error" in pod.lower() or "fail" in pod.lower() or "002" in pod:
        # Generate failure logs for testing RCA
        sample_logs = f"""[{timestamp}] INFO: Pod {pod} starting up...
[{timestamp}] INFO: Application: {app}
[{timestamp}] INFO: Bundle: {bundle}
[{timestamp}] INFO: Initializing container...
[{timestamp}] INFO: Loading configuration...
[{timestamp}] ERROR: Failed to connect to database at db.{bundle}.svc.cluster.local:5432
[{timestamp}] ERROR: Connection timeout after 30 seconds
[{timestamp}] WARN: Retrying database connection (attempt 1/3)...
[{timestamp}] ERROR: Connection failed: FATAL: password authentication failed for user "{app}_user"
[{timestamp}] WARN: Retrying database connection (attempt 2/3)...
[{timestamp}] ERROR: Connection failed: FATAL: database "{bundle}_db" does not exist
[{timestamp}] WARN: Retrying database connection (attempt 3/3)...
[{timestamp}] ERROR: Connection failed: connection to server at "db.{bundle}.svc.cluster.local" (10.96.0.15), port 5432 failed
[{timestamp}] FATAL: Unable to establish database connection after 3 attempts
[{timestamp}] ERROR: Application startup failed
[{timestamp}] ERROR: Container exit code: 1

--- Error Details ---
[{timestamp}] ERROR: DatabaseConnectionError: Could not connect to PostgreSQL database
[{timestamp}] ERROR: Stack trace:
  at DatabaseConnector.connect() /app/src/db/connector.js:45
  at Application.initialize() /app/src/app.js:23
  at startup() /app/src/index.js:12
[{timestamp}] ERROR: Environment variables check:
  DB_HOST: db.{bundle}.svc.cluster.local ✓
  DB_PORT: 5432 ✓
  DB_USER: {app}_user ✓
  DB_PASSWORD: [REDACTED] ✗ (possibly incorrect)
  DB_NAME: {bundle}_db ✗ (database does not exist)

--- Pod Status ---
Status: CrashLoopBackOff
Uptime: 0m 45s
Restart Count: 5
Last Exit Code: 1
Node: worker-node-002
Namespace: {bundle}
Image: {app}:v1.2.3-broken
"""
    elif "warn" in pod.lower() or "worker" in pod.lower():
        # Generate warning logs with performance issues
        sample_logs = f"""[{timestamp}] INFO: Pod {pod} starting up...
[{timestamp}] INFO: Application: {app}
[{timestamp}] INFO: Bundle: {bundle}
[{timestamp}] INFO: Initializing container...
[{timestamp}] INFO: Loading configuration...
[{timestamp}] INFO: Connecting to database...
[{timestamp}] WARN: Database connection slow (5.2s response time)
[{timestamp}] INFO: Database connection established
[{timestamp}] INFO: Starting web server on port 8080...
[{timestamp}] INFO: Health check endpoint available at /health
[{timestamp}] WARN: Application startup took 12.3 seconds (expected < 10s)
[{timestamp}] INFO: Application ready to serve requests
[{timestamp}] INFO: Pod {pod} is running
[{timestamp}] WARN: Memory usage: 1.8GB (approaching 2GB limit)
[{timestamp}] WARN: CPU usage: 85% (high load detected)
[{timestamp}] ERROR: Failed to process job #1247: timeout after 30s
[{timestamp}] WARN: Queue backlog growing: 45 pending jobs
[{timestamp}] ERROR: OutOfMemoryError in worker thread #3
[{timestamp}] WARN: Garbage collection taking longer than usual (2.1s)
[{timestamp}] ERROR: HTTP 500 Internal Server Error for /api/process-data
[{timestamp}] WARN: Response times degrading: avg 2.8s (SLA: < 1s)

--- Recent Activity ---
[{timestamp}] WARN: High error rate detected: 15% (threshold: 5%)
[{timestamp}] ERROR: Redis connection pool exhausted
[{timestamp}] WARN: Scaling recommendation: increase memory limit to 3GB
[{timestamp}] ERROR: Failed to send notification: SMTP timeout

--- Pod Status ---
Status: Running (Degraded)
Uptime: 1h 23m 15s
Restart Count: 2
Node: worker-node-003
Namespace: {bundle}
Image: {app}:latest
Memory Usage: 90% of 2GB limit
CPU Usage: 85%
"""
    else:
        # Generate successful logs
        sample_logs = f"""[{timestamp}] INFO: Pod {pod} starting up...
[{timestamp}] INFO: Application: {app}
[{timestamp}] INFO: Bundle: {bundle}
[{timestamp}] INFO: Initializing container...
[{timestamp}] INFO: Loading configuration...
[{timestamp}] INFO: Connecting to database...
[{timestamp}] INFO: Database connection established in 1.2s
[{timestamp}] INFO: Starting web server on port 8080...
[{timestamp}] INFO: Health check endpoint available at /health
[{timestamp}] INFO: Application ready to serve requests in 3.4s
[{timestamp}] INFO: Pod {pod} is running successfully
[{timestamp}] DEBUG: Memory usage: 256MB (12% of 2GB limit)
[{timestamp}] DEBUG: CPU usage: 5%
[{timestamp}] INFO: Processed 150 requests in the last minute
[{timestamp}] INFO: All systems operational
[{timestamp}] INFO: Database queries avg response time: 45ms
[{timestamp}] INFO: Cache hit ratio: 94%

--- Recent Activity ---
[{timestamp}] INFO: Received GET request to /api/status
[{timestamp}] INFO: Response sent with status 200 (12ms)
[{timestamp}] INFO: Received POST request to /api/data
[{timestamp}] INFO: Data processed successfully (89ms)
[{timestamp}] INFO: Response sent with status 201
[{timestamp}] INFO: Background job completed: data-sync-{bundle}
[{timestamp}] INFO: Health check passed: all dependencies healthy

--- Pod Status ---
Status: Running
Uptime: 2h 45m 30s
Restart Count: 0
Node: worker-node-001
Namespace: {bundle}
Image: {app}:latest
Memory Usage: 12% of 2GB limit
CPU Usage: 5%
Disk Usage: 35% of 10GB
"""

    return sample_logs


@require_POST
@csrf_exempt
def summarize_logs(request):
    log_text = request.POST.get("log_text", "").strip()
    
    if not log_text or log_text in ["Waiting for execution...", "Fetching pod logs..."]:
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
    
    if not log_text or log_text in ["Waiting for execution...", "Fetching pod logs..."]:
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
        
        # Check pods config (optional)
        pods_config_path = os.path.join("app", "static", "pods_config.json")
        pods_config_exists = os.path.exists(pods_config_path)
        
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "checks": {
                "together_api_configured": api_configured,
                "app_config_exists": config_exists,
                "logs_directory_exists": logs_dir_exists,
                "pods_config_exists": pods_config_exists
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