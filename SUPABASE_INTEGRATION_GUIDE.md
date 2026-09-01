# Supabase Integration Guide for Django

This guide explains how to use Supabase with your Django project (Rathna_Stores).

## Setup Instructions

### 1. Install Dependencies

First, install the required packages:

```bash
pip install -r requirements.txt
```

Or manually install:

```bash
pip install python-dotenv supabase postgrest-py
```

### 2. Environment Variables

Your `.env.local` file already contains the Supabase credentials:

```
SUPABASE_URL=https://kwdvnasbxnywrcyolcxj.supabase.co
SUPABASE_KEY=sb_publishable_P6eByhi84oWDjks8SMFKrQ_iZPP4Jlp
```

⚠️ **Security Note**: The `SUPABASE_KEY` is a publishable key (safe for frontend). For backend operations requiring admin access, you'll need the service role key from your Supabase dashboard.

### 3. Django Settings

The `settings.py` has been updated to:
- Load environment variables from `.env.local` using `python-dotenv`
- Store Supabase credentials in `SUPABASE_URL` and `SUPABASE_KEY`

### 4. Files Added/Modified

#### New Files Created:

1. **`Rathna_Stores/supabase_client.py`**
   - Provides `get_supabase_client()` function to initialize Supabase client
   - Usage: `from Rathna_Stores.supabase_client import get_supabase_client`

2. **`Rathna_Stores/supabase_utils.py`**
   - Helper functions for common Supabase operations:
     - `insert_to_supabase(table_name, data)`
     - `fetch_from_supabase(table_name, filters=None)`
     - `update_in_supabase(table_name, data, filters)`
     - `delete_from_supabase(table_name, filters)`

3. **`SUPABASE_EXAMPLES.py`**
   - Example Django views showing how to use Supabase in your API endpoints

4. **`requirements.txt`**
   - Lists all required packages

#### Modified Files:

- **`Rathna_Stores/settings.py`**
  - Added `import os` and `from dotenv import load_dotenv`
  - Added `load_dotenv()` to load environment variables
  - Added `SUPABASE_URL` and `SUPABASE_KEY` settings

## Usage Examples

### Example 1: Fetch Data

```python
from Rathna_Stores.supabase_utils import fetch_from_supabase

# Get all cakes
cakes = fetch_from_supabase('cakes')

# Get cake with specific ID
cake = fetch_from_supabase('cakes', {'id': 1})
```

### Example 2: Insert Data

```python
from Rathna_Stores.supabase_utils import insert_to_supabase

data = {
    'name': 'Chocolate Cake',
    'price': 25.99,
    'description': 'Delicious chocolate cake'
}
result = insert_to_supabase('cakes', data)
```

### Example 3: Update Data

```python
from Rathna_Stores.supabase_utils import update_in_supabase

new_data = {'price': 29.99}
result = update_in_supabase('cakes', new_data, {'id': 1})
```

### Example 4: Delete Data

```python
from Rathna_Stores.supabase_utils import delete_from_supabase

result = delete_from_supabase('cakes', {'id': 1})
```

### Example 5: Using in Django Views

See `SUPABASE_EXAMPLES.py` for full examples of how to use Supabase in your views.

## Creating Supabase Tables

1. Go to [Supabase Dashboard](https://app.supabase.com)
2. Select your project
3. Navigate to **SQL Editor** → **New Query**
4. Create tables for your app (example for cakes):

```sql
CREATE TABLE cakes (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  name TEXT NOT NULL,
  price DECIMAL(10, 2),
  description TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE customers (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  name TEXT NOT NULL,
  email TEXT UNIQUE,
  phone TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

## Syncing Django Models with Supabase

You have two options:

### Option A: Keep Django ORM + Use Supabase for Real-time Features
- Keep your Django models in SQLite for admin interface
- Use Supabase for specific tables that need real-time capabilities
- Sync data between them as needed

### Option B: Migrate to Supabase PostgreSQL
- Replace SQLite with Supabase PostgreSQL database
- Update `DATABASES` in `settings.py`:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'postgres',
        'USER': 'postgres',
        'PASSWORD': os.getenv('SUPABASE_DB_PASSWORD'),
        'HOST': os.getenv('SUPABASE_HOST'),
        'PORT': '5432',
    }
}
```

## Authentication with Supabase

For user authentication with Supabase:

```python
from Rathna_Stores.supabase_client import get_supabase_client

client = get_supabase_client()

# Sign up
auth_response = client.auth.sign_up({
    "email": "user@example.com",
    "password": "password123"
})

# Sign in
auth_response = client.auth.sign_in_with_password({
    "email": "user@example.com",
    "password": "password123"
})

# Sign out
client.auth.sign_out()
```

## Real-time Features

Supabase supports real-time subscriptions:

```python
from Rathna_Stores.supabase_client import get_supabase_client

client = get_supabase_client()

def callback(payload):
    print("Change received:", payload)

# Subscribe to changes
subscription = client.realtime.on(
    'postgres_changes',
    {'event': '*', 'schema': 'public', 'table': 'cakes'},
    callback
).subscribe()
```

## Troubleshooting

1. **ImportError: No module named 'dotenv'**
   - Run: `pip install python-dotenv`

2. **ImportError: No module named 'supabase'**
   - Run: `pip install supabase`

3. **SUPABASE_URL or SUPABASE_KEY not found**
   - Ensure `.env.local` file exists in project root
   - Verify the environment variables are set correctly
   - Restart your Django development server

4. **Permission Denied on Supabase**
   - Check your Supabase Row Level Security (RLS) policies
   - Ensure you're using the correct API key (publishable vs service role)

## API Documentation

- [Supabase Python Client](https://supabase.com/docs/reference/python/introduction)
- [Supabase REST API](https://supabase.com/docs/guides/api)
- [Django Documentation](https://docs.djangoproject.com/)

## Next Steps

1. Create tables in your Supabase project
2. Test the connection with the examples provided
3. Implement Supabase queries in your Django apps
4. Consider real-time features for live updates
5. Set up Row Level Security (RLS) policies for data protection
