"""
Supabase utilities for Django models
This module provides helper functions to interact with Supabase from Django
"""
from .supabase_client import get_supabase_client
from postgrest.exceptions import APIError


def insert_to_supabase(table_name: str, data: dict):
    """
    Insert data into a Supabase table
    
    Example:
        insert_to_supabase('users', {'name': 'John', 'email': 'john@example.com'})
    """
    try:
        client = get_supabase_client()
        response = client.table(table_name).insert(data).execute()
        return response.data
    except APIError as e:
        print(f"Error inserting into {table_name}: {e}")
        raise


def fetch_from_supabase(table_name: str, filters: dict = None):
    """
    Fetch data from a Supabase table with optional filters
    
    Example:
        fetch_from_supabase('users', {'id': 1})
    """
    try:
        client = get_supabase_client()
        query = client.table(table_name).select("*")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        response = query.execute()
        return response.data
    except APIError as e:
        print(f"Error fetching from {table_name}: {e}")
        raise


def update_in_supabase(table_name: str, data: dict, filters: dict):
    """
    Update data in a Supabase table
    
    Example:
        update_in_supabase('users', {'name': 'Jane'}, {'id': 1})
    """
    try:
        client = get_supabase_client()
        query = client.table(table_name).update(data)
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return response.data
    except APIError as e:
        print(f"Error updating {table_name}: {e}")
        raise


def delete_from_supabase(table_name: str, filters: dict):
    """
    Delete data from a Supabase table
    
    Example:
        delete_from_supabase('users', {'id': 1})
    """
    try:
        client = get_supabase_client()
        query = client.table(table_name).delete()
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return response.data
    except APIError as e:
        print(f"Error deleting from {table_name}: {e}")
        raise
