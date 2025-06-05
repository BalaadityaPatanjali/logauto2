import subprocess
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def index(request):
    context = {}
    if request.method == 'POST':
        app = request.POST.get('application')
        cluster = request.POST.get('cluster')
        testtype = request.POST.get('testtype')

        script_path = './app/scripts/run_test.sh'

        try:
            result = subprocess.run(
                [script_path, app, cluster, testtype],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            context['log'] = result.stdout
        except subprocess.CalledProcessError as e:
            context['log'] = f"Error:\n{e.stderr}"

        context.update({
            "selected_app": app,
            "selected_cluster": cluster,
            "selected_test": testtype
        })

    return render(request, 'app/index.html', context)
