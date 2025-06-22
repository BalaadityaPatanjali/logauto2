import os
import requests
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")

@csrf_exempt
def index(request):
    context = {}

    if request.method == 'POST':
        app = request.POST.get('application')
        cluster = request.POST.get('cluster')
        testtype = request.POST.get('testtype')

        log_file_name = f"{app}-{testtype}.log"
        log_file_path = f"app/static/logs/{log_file_name}"

        if os.path.exists(log_file_path):
            with open(log_file_path, "r") as f:
                context['log'] = f.read()
            context['log_file_name'] = log_file_name
        else:
            context['log'] = f"❌ Log file not found: {log_file_path}"
            context['log_file_name'] = None

        context.update({
            "selected_app": app,
            "selected_cluster": cluster,
            "selected_test": testtype,
        })

    return render(request, 'app/index.html', context)

@csrf_exempt
def summarize_logs(request):
    if request.method == "POST":
        log_text = request.POST.get("log_text")

        if not log_text:
            return JsonResponse({"summary": "No logs to summarize."})

        try:
            TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
            headers = {
                "Authorization": f"Bearer {TOGETHER_API_KEY}",
                "Content-Type": "application/json"
            }

            data = {
                "model": "meta-llama/Llama-3-8b-chat-hf",
                "messages": [
                    {
                        "role": "system",
                        "content": "You're a helpful assistant who summarizes technical logs into concise explanations."
                    },
                    {
                        "role": "user",
                        "content": f"Summarize this log:\n{log_text}"
                    }
                ],
                "max_tokens": 300,
                "temperature": 0.7
            }

            response = requests.post("https://api.together.xyz/v1/chat/completions", headers=headers, json=data)

            if response.status_code == 200:
                result = response.json()
                summary = result["choices"][0]["message"]["content"]
                return JsonResponse({"summary": summary})
            else:
                return JsonResponse({"summary": f"❌ API error: {response.status_code}"})
        except Exception as e:
            return JsonResponse({"summary": f"❌ Unexpected error: {str(e)}"})

@csrf_exempt
def analyze_logs(request):
    if request.method == "POST":
        log_text = request.POST.get("log_text", "")

        if not log_text:
            return JsonResponse({"analysis": "❌ No logs provided for analysis."})

        try:
            headers = {
                "Authorization": f"Bearer {TOGETHER_API_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "meta-llama/Llama-3-8b-chat-hf",  # or any available model
                "max_tokens": 512,
                "prompt": f"Find the root cause of the error in this log:\n{log_text}"
            }

            response = requests.post(
                "https://api.together.xyz/v1/completions",
                headers=headers,
                json=payload,
                timeout=15
            )

            if response.status_code != 200:
                return JsonResponse({"analysis": f"❌ API error: {response.status_code}"})

            result = response.json()
            output = result.get("choices", [{}])[0].get("text", "").strip()
            return JsonResponse({"analysis": output or "❌ No response from AI."})

        except requests.exceptions.Timeout:
            return JsonResponse({"analysis": "❌ Request timed out."})
        except Exception as e:
            return JsonResponse({"analysis": f"❌ Error: {str(e)}"})