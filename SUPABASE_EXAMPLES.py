"""
Example: Using Supabase with Django Views

This file demonstrates how to use Supabase in your Django views.
You can use these patterns in your actual views.py files.
"""

from django.http import JsonResponse
from django.views import View
from Rathna_Stores.supabase_utils import (
    insert_to_supabase,
    fetch_from_supabase,
    update_in_supabase,
    delete_from_supabase,
)


# Example 1: Fetch data from Supabase and return as JSON
def get_cakes(request):
    """
    Fetch all cakes from Supabase
    Example route: /api/cakes/
    """
    try:
        cakes = fetch_from_supabase('cakes')
        return JsonResponse({'status': 'success', 'data': cakes})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# Example 2: Fetch specific cake by ID
def get_cake_detail(request, cake_id):
    """
    Fetch a specific cake from Supabase
    Example route: /api/cakes/<int:cake_id>/
    """
    try:
        cake = fetch_from_supabase('cakes', {'id': cake_id})
        if cake:
            return JsonResponse({'status': 'success', 'data': cake[0]})
        else:
            return JsonResponse({'status': 'error', 'message': 'Cake not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# Example 3: Create a new cake in Supabase
def create_cake(request):
    """
    Create a new cake in Supabase
    Example: POST /api/cakes/
    Expected JSON body:
    {
        "name": "Chocolate Cake",
        "price": 25.99,
        "description": "Delicious chocolate cake"
    }
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        
        result = insert_to_supabase('cakes', data)
        return JsonResponse({'status': 'success', 'data': result})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# Example 4: Update a cake in Supabase
def update_cake(request, cake_id):
    """
    Update a cake in Supabase
    Example: PUT /api/cakes/<int:cake_id>/
    Expected JSON body:
    {
        "price": 29.99,
        "description": "Updated description"
    }
    """
    if request.method != 'PUT':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        
        result = update_in_supabase('cakes', data, {'id': cake_id})
        return JsonResponse({'status': 'success', 'data': result})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# Example 5: Delete a cake from Supabase
def delete_cake(request, cake_id):
    """
    Delete a cake from Supabase
    Example: DELETE /api/cakes/<int:cake_id>/
    """
    if request.method != 'DELETE':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)
    
    try:
        result = delete_from_supabase('cakes', {'id': cake_id})
        return JsonResponse({'status': 'success', 'message': 'Cake deleted'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
