from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render

from apps.email_ai.models import GmailCredential


def register_view(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Automatically log the user in after registration
            login(request, user)
            return redirect("/meetings")  # Redirect to homepage
    else:
        form = UserCreationForm()

    return render(request, "users/register.html", {"form": form})


@login_required
def profile_view(request):
    return render(request, "users/profile.html")


@login_required
def settings_view(request):
    gmail_connected = GmailCredential.objects.filter(user=request.user).exists()
    return render(
        request,
        "users/settings.html",
        {"gmail_connected": gmail_connected},
    )
