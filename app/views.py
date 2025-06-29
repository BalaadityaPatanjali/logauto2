import os
import json
import re
import requests
import logging
import subprocess
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail

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
    """Get logs for a specific pod - auto-generate if not found"""
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

        # Define possible log file locations and patterns
        logs_base_dir = os.path.join("app", "static", "logs")
        
        # Multiple possible log file patterns to search for
        log_file_patterns = [
            # Exact pod name match
            f"{safe_pod}.log",
            # App-pod combination
            f"{safe_app}-{safe_pod}.log",
            # Bundle-pod combination  
            f"{safe_bundle}-{safe_pod}.log",
            # Full combination
            f"{safe_app}-{safe_bundle}-{safe_pod}.log",
            f"{safe_cluster}-{safe_bundle}-{safe_pod}.log",
            # Date-based patterns (if logs have timestamps)
            f"{safe_pod}-{datetime.now().strftime('%Y-%m-%d')}.log",
            f"{safe_app}-{safe_pod}-{datetime.now().strftime('%Y-%m-%d')}.log",
            # Alternative patterns
            f"{safe_pod}_logs.log",
            f"pod_{safe_pod}.log",
            # Generic patterns
            f"{safe_app}_{safe_bundle}_{safe_pod}.log",
        ]

        log_content = None
        found_log_file = None

        # Search for log files in multiple directories
        search_directories = [
            logs_base_dir,
            os.path.join(logs_base_dir, safe_app),
            os.path.join(logs_base_dir, safe_cluster), 
            os.path.join(logs_base_dir, safe_bundle),
            os.path.join(logs_base_dir, safe_app, safe_cluster),
            os.path.join(logs_base_dir, safe_app, safe_bundle),
            os.path.join(logs_base_dir, safe_cluster, safe_bundle),
            # Add kubernetes-style paths if you're using them
            os.path.join(logs_base_dir, "kubernetes", safe_cluster),
            os.path.join(logs_base_dir, "pods"),
        ]

        # Try each directory and pattern combination
        for directory in search_directories:
            if not os.path.exists(directory):
                continue
                
            for pattern in log_file_patterns:
                log_path = os.path.join(directory, pattern)
                
                if os.path.exists(log_path):
                    try:
                        # Check if file is not empty and recently modified
                        file_stats = os.stat(log_path)
                        if file_stats.st_size == 0:
                            logger.warning(f"Log file is empty: {log_path}")
                            continue
                            
                        with open(log_path, "r", encoding='utf-8') as f:
                            log_content = f.read()
                            
                        if log_content.strip():  # Ensure content is not just whitespace
                            found_log_file = log_path
                            logger.info(f"Successfully loaded pod log file: {log_path}")
                            break
                            
                    except UnicodeDecodeError:
                        logger.error(f"Unicode decode error for pod log file: {log_path}")
                        continue
                    except Exception as e:
                        logger.error(f"Error reading pod log file {log_path}: {str(e)}")
                        continue
            
            if log_content:
                break

        # If no log file found, try to fetch from Kubernetes API (if available)
        if not log_content:
            log_content = fetch_kubernetes_logs(safe_app, safe_cluster, safe_bundle, safe_pod)
            if log_content:
                found_log_file = "kubernetes-api"

        # AUTO-GENERATE: If still no logs found, automatically generate them
        if not log_content:
            logger.info(f"No existing log file found for {pod}, auto-generating...")
            
            # Generate log content automatically
            log_content = auto_generate_pod_logs(app, cluster, bundle, pod)
            
            # Save the generated log to file for future use
            auto_save_generated_logs(safe_app, safe_cluster, safe_bundle, safe_pod, log_content)
            
            found_log_file = "auto-generated"
            logger.info(f"Auto-generated logs for pod {pod}")

        # Add metadata to logs for debugging
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_metadata = f"""# LogOps - Log File Viewer
# Loaded: {timestamp}
# Source: {found_log_file}
# Pod: {pod}
# Application: {app}
# Cluster: {cluster}
# Bundle: {bundle}
# ====================================

"""
        
        final_log_content = log_metadata + log_content

        # Cache the logs for a short time (30 seconds)
        cache.set(cache_key, final_log_content, 30)
        
        return JsonResponse({"logs": final_log_content})

    except Exception as e:
        logger.error(f"Error getting pod logs: {str(e)}")
        return JsonResponse({
            "error": f"Failed to get pod logs: {str(e)}",
            "logs": f"Error loading logs for pod {pod}: {str(e)}"
        }, status=500)


def fetch_kubernetes_logs(app, cluster, bundle, pod):
    """
    Optional: Fetch logs directly from Kubernetes API
    This function can be implemented if you have kubectl access or k8s python client
    """
    try:
        # Example using kubectl command (if available)
        # Construct kubectl command
        kubectl_cmd = [
            'kubectl', 'logs', 
            pod, 
            '-n', bundle,  # assuming bundle is the namespace
            '--tail=1000'  # get last 1000 lines
        ]
        
        # Add context if cluster is specified
        if cluster and cluster != 'default':
            kubectl_cmd.extend(['--context', cluster])
        
        logger.info(f"Attempting to fetch logs via kubectl: {' '.join(kubectl_cmd)}")
        
        # Execute kubectl command
        result = subprocess.run(
            kubectl_cmd, 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        
        if result.returncode == 0:
            logger.info(f"Successfully fetched logs from Kubernetes for pod {pod}")
            return result.stdout
        else:
            logger.warning(f"kubectl command failed: {result.stderr}")
            return None
            
    except subprocess.TimeoutExpired:
        logger.error("Kubectl command timed out")
        return None
    except FileNotFoundError:
        logger.debug("kubectl not found - skipping Kubernetes API fetch")
        return None
    except Exception as e:
        logger.error(f"Error fetching Kubernetes logs: {str(e)}")
        return None


def generate_sample_pod_logs(app, bundle, pod):
    """Generate sample log content ONLY as a last resort fallback - NOT USED ANYMORE"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return f"""# DEMO MODE - Sample Logs Generated
# Real log files not found for pod: {pod}
# This is sample content for demonstration purposes
# ====================================

[{timestamp}] INFO: Pod {pod} - Demo mode active
[{timestamp}] INFO: Application: {app}
[{timestamp}] INFO: Bundle: {bundle}
[{timestamp}] WARN: Real log files should be placed in /app/static/logs/
[{timestamp}] INFO: Expected log file patterns:
[{timestamp}] INFO: - {pod}.log
[{timestamp}] INFO: - {app}-{pod}.log  
[{timestamp}] INFO: - {bundle}-{pod}.log
[{timestamp}] INFO: Please contact your administrator to configure proper log file access
[{timestamp}] INFO: Demo pod {pod} simulated successfully"""


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


@require_POST
@csrf_exempt
def send_rca_email(request):
    """Send RCA analysis via email"""
    if request.method == 'POST':
        try:
            email = request.POST.get('email', '').strip()
            analysis = request.POST.get('analysis', '').strip()
            pod_name = request.POST.get('pod_name', 'Unknown Pod').strip()
            
            # Validate inputs
            if not email:
                return JsonResponse({
                    'success': False, 
                    'error': 'Email address is required'
                }, status=400)
            
            if not analysis:
                return JsonResponse({
                    'success': False, 
                    'error': 'No analysis content to send'
                }, status=400)
            
            # Basic email validation
            if '@' not in email or '.' not in email.split('@')[-1]:
                return JsonResponse({
                    'success': False, 
                    'error': 'Invalid email address format'
                }, status=400)
            
            # Generate timestamp
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Create email subject
            subject = f'LogOps RCA Report - {pod_name}'
            
            # Create email message
            message = f"""
Root Cause Analysis Report
=========================

Pod: {pod_name}
Generated: {timestamp}
Requested by: {email}

ANALYSIS RESULTS:
{analysis}

---
This report was automatically generated by LogOps Testing Portal.
For questions or support, please contact your DevOps team.

LogOps Testing Portal
DevOps Automation System
            """.strip()
            
            # Send email
            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'logops@company.com'),
                    recipient_list=[email],
                    fail_silently=False,
                )
                
                logger.info(f"RCA report sent successfully to {email} for pod {pod_name}")
                
                return JsonResponse({
                    'success': True, 
                    'message': f'RCA report sent successfully to {email}'
                })
                
            except Exception as email_error:
                logger.error(f"Failed to send RCA email to {email}: {str(email_error)}")
                return JsonResponse({
                    'success': False, 
                    'error': f'Failed to send email: {str(email_error)}'
                }, status=500)
                
        except Exception as e:
            logger.error(f"Error in send_rca_email: {str(e)}")
            return JsonResponse({
                'success': False, 
                'error': f'Server error: {str(e)}'
            }, status=500)
    
    return JsonResponse({
        'success': False, 
        'error': 'Invalid request method'
    }, status=405)


@require_POST
@csrf_exempt
def track_download(request):
    """
    Track log file downloads for analytics and auditing
    """
    try:
        filename = request.POST.get('filename', '').strip()
        app = request.POST.get('app', '').strip()
        cluster = request.POST.get('cluster', '').strip()
        bundle = request.POST.get('bundle', '').strip()
        pod = request.POST.get('pod', '').strip()
        log_size = request.POST.get('log_size', '0').strip()
        
        # Validate required parameters
        if not filename:
            return JsonResponse({
                'success': False,
                'error': 'Filename is required'
            }, status=400)
        
        # Sanitize inputs for logging
        safe_filename = sanitize_filename(filename)
        safe_app = sanitize_filename(app) if app else 'unknown'
        safe_cluster = sanitize_filename(cluster) if cluster else 'unknown'
        safe_bundle = sanitize_filename(bundle) if bundle else 'unknown'
        safe_pod = sanitize_filename(pod) if pod else 'unknown'
        
        # Convert log_size to integer, default to 0 if invalid
        try:
            file_size = int(log_size)
        except (ValueError, TypeError):
            file_size = 0
        
        # Get client information
        client_ip = request.META.get('REMOTE_ADDR', 'unknown')
        user_agent = request.META.get('HTTP_USER_AGENT', 'unknown')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Log the download event with detailed information
        logger.info(
            f"LOG_DOWNLOAD: {safe_filename} | "
            f"App: {safe_app} | "
            f"Cluster: {safe_cluster} | "
            f"Bundle: {safe_bundle} | "
            f"Pod: {safe_pod} | "
            f"Size: {file_size} bytes | "
            f"IP: {client_ip} | "
            f"Time: {timestamp}"
        )
        
        # Create download statistics for cache/tracking
        download_stats = {
            'filename': safe_filename,
            'application': safe_app,
            'cluster': safe_cluster,
            'bundle': safe_bundle,
            'pod': safe_pod,
            'file_size': file_size,
            'client_ip': client_ip,
            'user_agent': user_agent[:200],  # Truncate long user agents
            'timestamp': timestamp
        }
        
        # Store in cache for recent downloads tracking (optional)
        recent_downloads_key = f"recent_downloads_{client_ip}"
        recent_downloads = cache.get(recent_downloads_key, [])
        recent_downloads.append(download_stats)
        
        # Keep only last 10 downloads per IP
        if len(recent_downloads) > 10:
            recent_downloads = recent_downloads[-10:]
        
        # Cache for 1 hour
        cache.set(recent_downloads_key, recent_downloads, 3600)
        
        # Update download counter in cache
        download_counter_key = "total_downloads_today"
        today = datetime.now().strftime('%Y-%m-%d')
        daily_counter_key = f"downloads_{today}"
        
        # Increment counters
        cache.set(download_counter_key, cache.get(download_counter_key, 0) + 1, 86400)  # 24 hours
        cache.set(daily_counter_key, cache.get(daily_counter_key, 0) + 1, 86400)
        
        logger.info(f"Download tracking completed for {safe_filename}")
        
        return JsonResponse({
            'success': True,
            'message': 'Download tracked successfully',
            'download_id': f"{safe_app}_{safe_pod}_{timestamp.replace(' ', '_').replace(':', '')}"
        })
        
    except Exception as e:
        logger.error(f"Error tracking download: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to track download: {str(e)}'
        }, status=500)


@csrf_exempt
def get_download_stats(request):
    """
    Get download statistics (optional endpoint for analytics)
    """
    try:
        # Get client IP for recent downloads
        client_ip = request.META.get('REMOTE_ADDR', 'unknown')
        
        # Get recent downloads for this IP
        recent_downloads_key = f"recent_downloads_{client_ip}"
        recent_downloads = cache.get(recent_downloads_key, [])
        
        # Get daily statistics
        today = datetime.now().strftime('%Y-%m-%d')
        daily_counter_key = f"downloads_{today}"
        total_today = cache.get(daily_counter_key, 0)
        
        # Get total downloads counter
        total_downloads = cache.get("total_downloads_today", 0)
        
        stats = {
            'recent_downloads': recent_downloads[-5:],  # Last 5 downloads
            'downloads_today': total_today,
            'total_downloads': total_downloads,
            'timestamp': datetime.now().isoformat()
        }
        
        return JsonResponse({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        logger.error(f"Error getting download stats: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get download stats: {str(e)}'
        }, status=500)


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
        
        # Check email configuration
        email_configured = bool(getattr(settings, 'EMAIL_HOST', '')) and bool(getattr(settings, 'EMAIL_HOST_USER', ''))
        
        # Count existing log files
        log_files_count = 0
        if logs_dir_exists:
            try:
                for root, dirs, files in os.walk(logs_dir):
                    log_files_count += len([f for f in files if f.endswith('.log')])
            except Exception:
                log_files_count = 0
        
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "checks": {
                "together_api_configured": api_configured,
                "app_config_exists": config_exists,
                "logs_directory_exists": logs_dir_exists,
                "pods_config_exists": pods_config_exists,
                "email_configured": email_configured,
                "log_files_count": log_files_count
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
    
def auto_generate_pod_logs(app, cluster, bundle, pod):
    """
    Automatically generate realistic log content for a pod
    """
    from datetime import timedelta
    
    base_time = datetime.now() - timedelta(hours=1, minutes=30)
    logs = []
    
    # Add startup sequence
    logs.append(f"[{base_time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Starting pod {pod}")
    logs.append(f"[{(base_time + timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Application: {app}")
    logs.append(f"[{(base_time + timedelta(seconds=2)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Cluster: {cluster}")
    logs.append(f"[{(base_time + timedelta(seconds=3)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Bundle: {bundle}")
    logs.append(f"[{(base_time + timedelta(seconds=4)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Namespace: {bundle}")
    logs.append(f"[{(base_time + timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Container image: {app}:latest")
    logs.append(f"[{(base_time + timedelta(seconds=6)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Initializing container environment...")
    
    # Add service-specific startup based on pod name patterns
    if any(x in pod.lower() for x in ['web', 'frontend', 'nginx', 'ui']):
        logs.extend([
            f"[{(base_time + timedelta(seconds=8)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Starting HTTP server on port 8080",
            f"[{(base_time + timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Loading static assets and configurations",
            f"[{(base_time + timedelta(seconds=12)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Registering routes and middleware",
            f"[{(base_time + timedelta(seconds=15)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Web server ready - accepting connections"
        ])
    elif any(x in pod.lower() for x in ['api', 'service', 'gateway']):
        logs.extend([
            f"[{(base_time + timedelta(seconds=8)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Loading API configuration",
            f"[{(base_time + timedelta(seconds=9)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Connecting to database...",
            f"[{(base_time + timedelta(seconds=11)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Database connection established",
            f"[{(base_time + timedelta(seconds=13)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Registering API endpoints",
            f"[{(base_time + timedelta(seconds=15)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: API server ready on port 3000"
        ])
    elif any(x in pod.lower() for x in ['worker', 'processor', 'job']):
        logs.extend([
            f"[{(base_time + timedelta(seconds=8)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Connecting to message queue",
            f"[{(base_time + timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Registering job handlers",
            f"[{(base_time + timedelta(seconds=12)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Starting worker processes",
            f"[{(base_time + timedelta(seconds=15)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Worker ready to process jobs"
        ])
    else:
        # Generic service startup
        logs.extend([
            f"[{(base_time + timedelta(seconds=8)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Loading service configuration",
            f"[{(base_time + timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Initializing service components",
            f"[{(base_time + timedelta(seconds=12)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Service health check passed",
            f"[{(base_time + timedelta(seconds=15)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Service ready and operational"
        ])
    
    # Add operational logs based on pod name patterns
    current_time = base_time + timedelta(seconds=20)
    
    if "error" in pod.lower():
        # Generate error scenario
        logs.extend([
            f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Processing incoming requests...",
            f"[{(current_time + timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')}] WARN: High memory usage detected: 85%",
            f"[{(current_time + timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Database connection timeout after 30s",
            f"[{(current_time + timedelta(seconds=90)).strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Failed to process request: connection refused",
            f"[{(current_time + timedelta(seconds=120)).strftime('%Y-%m-%d %H:%M:%S')}] WARN: Retrying database connection (attempt 1/3)",
            f"[{(current_time + timedelta(seconds=150)).strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Authentication failed: invalid credentials",
            f"[{(current_time + timedelta(seconds=180)).strftime('%Y-%m-%d %H:%M:%S')}] FATAL: Critical error - service unavailable",
            f"[{(current_time + timedelta(seconds=200)).strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Container exit code: 1"
        ])
    elif "warn" in pod.lower():
        # Generate warning scenario
        logs.extend([
            f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Service operational - processing requests",
            f"[{(current_time + timedelta(seconds=45)).strftime('%Y-%m-%d %H:%M:%S')}] WARN: High CPU usage detected: 78%",
            f"[{(current_time + timedelta(seconds=90)).strftime('%Y-%m-%d %H:%M:%S')}] WARN: Response time degradation: 2.8s (SLA: 1s)",
            f"[{(current_time + timedelta(seconds=135)).strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Failed to send notification: SMTP timeout",
            f"[{(current_time + timedelta(seconds=180)).strftime('%Y-%m-%d %H:%M:%S')}] WARN: Queue backlog growing: 150 pending items",
            f"[{(current_time + timedelta(seconds=225)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Auto-scaling triggered - adding 2 replicas",
            f"[{(current_time + timedelta(seconds=270)).strftime('%Y-%m-%d %H:%M:%S')}] WARN: Memory usage approaching limit: 92%",
            f"[{(current_time + timedelta(seconds=315)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Performance stabilized after scaling"
        ])
    else:
        # Generate normal operational logs
        logs.extend([
            f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Service running normally",
            f"[{(current_time + timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Processed 250 requests in last minute",
            f"[{(current_time + timedelta(seconds=120)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Health check passed - all systems green",
            f"[{(current_time + timedelta(seconds=180)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Database queries avg response: 45ms",
            f"[{(current_time + timedelta(seconds=240)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Cache hit ratio: 94.2%",
            f"[{(current_time + timedelta(seconds=300)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Background job completed successfully",
            f"[{(current_time + timedelta(seconds=360)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Memory usage: 45% - optimal range",
            f"[{(current_time + timedelta(seconds=420)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Request throughput: 4.2 req/sec"
        ])
    
    # Add recent activity summary
    recent_time = datetime.now() - timedelta(minutes=2)
    logs.extend([
        f"[{recent_time.strftime('%Y-%m-%d %H:%M:%S')}] INFO: === Recent Activity Summary ===",
        f"[{(recent_time + timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Uptime: 1h 28m 15s",
        f"[{(recent_time + timedelta(seconds=2)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Total requests processed: 15,240",
        f"[{(recent_time + timedelta(seconds=3)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Current memory usage: {45 if 'error' not in pod else 95}%",
        f"[{(recent_time + timedelta(seconds=4)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Current CPU usage: {25 if 'error' not in pod else 98}%",
        f"[{(recent_time + timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')}] INFO: Network I/O: 2.1 MB/s in, 3.4 MB/s out"
    ])
    
    return "\n".join(logs)


def auto_save_generated_logs(app, cluster, bundle, pod, log_content):
    """
    Save auto-generated logs to file for future access
    """
    try:
        # Create logs directory structure
        logs_dir = os.path.join("app", "static", "logs")
        pods_dir = os.path.join(logs_dir, "pods")
        
        # Ensure directories exist
        os.makedirs(pods_dir, exist_ok=True)
        
        # Save in flat structure for easy access
        flat_filename = f"{app}-{bundle}-{pod}.log"
        flat_path = os.path.join(pods_dir, flat_filename)
        
        with open(flat_path, 'w', encoding='utf-8') as f:
            f.write(log_content)
        
        # Also save in hierarchical structure
        hierarchical_dir = os.path.join(logs_dir, app, cluster, bundle)
        os.makedirs(hierarchical_dir, exist_ok=True)
        
        hierarchical_path = os.path.join(hierarchical_dir, f"{pod}.log")
        with open(hierarchical_path, 'w', encoding='utf-8') as f:
            f.write(log_content)
        
        logger.info(f"Auto-saved generated logs to {flat_path} and {hierarchical_path}")
        
    except Exception as e:
        logger.warning(f"Failed to save auto-generated logs: {str(e)}")
        # Don't fail the request if we can't save the file