from datetime import date
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
import google.generativeai as genai
import os
from dotenv import load_dotenv
import re
from .forms import WorkoutForm
from django.forms import formset_factory
from django.utils.dateparse import parse_date  # Add this import at the top if not already present
from aiworkout.models import Workout, Badge, UserBadge
from django.db.models import Sum
from .leaderboard_utils import get_leaderboard_by_workouts_completed

@login_required
def badges_view(request):
    user = request.user
    earned = Badge.objects.filter(userbadge__user=user)
    unearned = Badge.objects.exclude(pk__in=earned.values_list('pk', flat=True))

    return render(request, 'badges.html', {
        'earned_badges': earned,
        'unearned_badges': unearned,
    })


load_dotenv()  # Loads .env file

api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-1.5-flash")


def workout_completed(request):
    if request.method == "POST":
        return render(request, "aiworkout/completed.html")
    return redirect("aiworkout")

def check_and_award_badges(user):
    for badge in Badge.objects.all():
        total = Workout.objects.filter(
            user=user,
            workout_type__icontains=badge.rule_type
        ).aggregate(Sum('reps'))['reps__sum'] or 0

        if total >= badge.threshold:
            already_earned = UserBadge.objects.filter(user=user, badge=badge).exists()
            if not already_earned:
                UserBadge.objects.create(user=user, badge=badge)



@login_required
def aiworkout_view(request):
    workout_plan = None
    exercises = []
  
    if request.method == "POST":
        user = request.user
        try:
            skill_level = user.userprofile.skill_level
            categories = user.userprofile.goals
        except AttributeError:
            
            skill_level = "beginner"
            categories = ["strength", "cardio"]

        categories_str = ", ".join(categories)

        prompt = (
            f"Create a personalized workout for the day for a user with {skill_level} experience. "
            f"Their preferred workout categories are: {categories_str}. "
            "Start with a 2-3 sentence intro about scientific muscle groups targeted and the skill level and categories this workout supports. "
            "Use motivating language and mention that it is personalized for the user. FitJacket hopes they enjoy it. "
            "Include at least 5 exercises with: name, sets, reps, rest time, and whether equipment is needed. "
            "Format clearly without asterisks, markdown, or bolding. Avoid extra spacing after exercise numbers. "
            "Make it a little scientific. For the excercise name, just have the name, DO NOT use Parenthesis with extra descriptions whatsoever."
            " Do Not use any description like (optional) or specific measurements. Follow the format below:\n\n"

            "Example:\n"
            "This workout focuses on building foundational strength and improving your explosive power. "
            "It targets major muscle groups like your legs, glutes, and core, perfect for beginners interested in stretching and plyometrics. "
            "FitJacket hopes you enjoy this personalized workout designed just for you!\n\n"

            "Exercise 1: Standing Quad Stretch\n"
            "Sets: 3\n"
            "Reps: 15 seconds hold per leg\n"
            "Rest: 30 seconds\n"
            "Equipment: None\n\n"

            "Exercise 2: Jumping Jacks\n"
            "Sets: 3\n"
            "Reps: 20\n"
            "Rest: 60 seconds\n"
            "Equipment: None\n\n"

            "Exercise 3: Hamstring Stretch (lying down)\n"
            "Sets: 3\n"
            "Reps: 15 seconds hold per leg\n"
            "Rest: 30 seconds\n"
            "Equipment: None\n\n"

            "Exercise 4: Butt Kicks\n"
            "Sets: 3\n"
            "Reps: 20\n"
            "Rest: 60 seconds\n"
            "Equipment: None\n\n"

            "Exercise 5: Squat Jumps\n"
            "Sets: 3\n"
            "Reps: 10\n"
            "Rest: 90 seconds\n"
            "Equipment: None\n\n"

            "End with a short motivational message like: Remember to listen to your body and take breaks when needed. You've got this!"

            "Include a few scientific labels for muscles."
        )

        try:
            response = model.generate_content(prompt)
            workout_plan = response.text.replace("Exercise 1\n\n", "Exercise 1\n").strip()
            exercises = parse_exercises(workout_plan)
           
        except Exception as e:
            workout_plan = "Something went wrong. Please try again."
            exercises = []

        else:
            print(">>> No POST request made. Did you forget to wrap the button in a form?")

        print(">>> Final workout_plan:", workout_plan)
        request.session['parsed_exercises'] = exercises  # Save to session


    return render(request, "aiworkout/show.html", {
        "workout_plan": workout_plan,
        "parsed_exercises": exercises,
        "today": date.today()
    })


@login_required
def log_workout(request):
    WorkoutFormSet = formset_factory(WorkoutForm, extra=5, max_num=5)

    if request.method == 'POST':
        formset = WorkoutFormSet(request.POST)
        if formset.is_valid():
             for form in formset:
                if form.cleaned_data:
                    workout_type = form.cleaned_data['workout_type']
                    sets = form.cleaned_data['sets']
                    reps = form.cleaned_data['reps']
                    rest = form.cleaned_data.get('rest', '')
                    equipment = form.cleaned_data.get('equipment', '')
                    date_logged = form.cleaned_data.get('date', date.today())

                    # Check if workout exists
                    existing = Workout.objects.filter(
                        user=request.user,
                        workout_type=workout_type,
                        date=date_logged
                    ).first()

                    if existing:
                        existing.sets += sets
                        existing.reps += reps
                        existing.save()
                    else:
                        workout = form.save(commit=False)
                        workout.user = request.user
                        workout.save()

        check_and_award_badges(request.user)
        return redirect('workout_chart')

    else:
        # Prefill with exercises from session (from Gemini)
        parsed = request.session.pop('parsed_exercises', [])
        initial_data = []
        for ex in parsed:
            initial_data.append({
                'workout_type': ex.get('workout_type'),
                'sets': ex.get('sets', 1),
                'reps': int(re.findall(r'\d+', str(ex.get('reps', '0')))[0]) if re.findall(r'\d+', str(ex.get('reps', '0'))) else 0,
                'rest': ex.get('rest', ''),
                'equipment': ex.get('equipment', ''),
                'date': date.today()
            })
        formset = WorkoutFormSet(initial=initial_data)

    return render(request, 'aiworkout/log_workout.html', {'formset': formset})


@login_required
def workout_chart(request):
    workouts = Workout.objects.filter(user=request.user).order_by('date')

    chart_data = {}
    for workout in workouts:
        date_key = str(workout.date)
        if date_key not in chart_data:
            chart_data[date_key] = {}
        chart_data[date_key][workout.workout_type] = chart_data[date_key].get(workout.workout_type, 0) + workout.reps

    return render(request, 'aiworkout/workout_chart.html', {'chart_data': chart_data})



def workout_completed(request):
    if request.method == "POST":
        return render(request, "aiworkout/completed.html")
    return redirect("aiworkout")



def parse_exercises(text):
    # Match each "Exercise X: Name\nSets: ...\nReps: ...\n..." block
    blocks = re.findall(r"Exercise \d+: (.*?)\nSets: (\d+)\nReps: ([\w\- ]+)\nRest: (\d+ seconds)\nEquipment: (.*?)\n?", text)
    parsed = []
    for name, sets, reps, rest, equipment in blocks:
        parsed.append({
            "workout_type": name.strip(),
            "sets": int(sets),
            "reps": reps.strip(),
            "rest": rest.strip(),
            "equipment": equipment.strip()
        })
    return parsed

# --- Bulk Save View ---
@login_required
def log_bulk_workout(request):
    if request.method == "POST":
        workout_types = request.POST.getlist('workout_type')
        reps_list = request.POST.getlist('reps')
        date_str = request.POST.getlist('date')[0] if request.POST.getlist('date') else str(date.today())
        workout_date = parse_date(date_str)

        for wt, reps in zip(workout_types, reps_list):
            try:
                rep_value = int(reps.split('-')[0].split()[0])  # Extract the first number from reps
            except Exception:
                rep_value = 0  # Default fallback

            # 🔁 Check for existing entry for same user, workout type, and date
            existing_workout = Workout.objects.filter(
                user=request.user,
                workout_type=wt,
                date=workout_date
            ).first()

            if existing_workout:
                existing_workout.reps += rep_value
                existing_workout.save()
            else:
                Workout.objects.create(
                    user=request.user,
                    workout_type=wt,
                    reps=rep_value,
                    date=workout_date
                )
        check_and_award_badges(request.user)
        return redirect('workout_chart')    
    return redirect('aiworkout')

@login_required
def leaderboard_view(request):
    user = request.user
    leaderboard = get_leaderboard_by_workouts_completed(user)
    return render(request, 'leaderboard.html', {'leaderboard': leaderboard})