"""
SmartSuite Permissions Audit Tool
Version 1.3 - Permissions Export

This script exports SmartSuite permissions data to CSV format.
Requires: python3 -m pip install requests

API Key Generation: https://help.smartsuite.com/en/articles/4855681-generating-an-api-key
Create a config.py file with:
    TOKEN = "your_api_token"
    ACCOUNT_ID = "your_account_id"
    DEST_FOLDER = Path("path/to/destination")
"""

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# Add home directory to path to import config from ~/config.py
home_config_path = Path.home() / "config.py"
if home_config_path.exists():
    sys.path.insert(0, str(Path.home()))
    from config import TOKEN, ACCOUNT_ID, DEST_FOLDER
else:
    # Fallback to local config.py if ~/config.py doesn't exist
    from config import TOKEN, ACCOUNT_ID, DEST_FOLDER

# Configuration
BASE_URL = "https://app.smartsuite.com/api/"
AUTH_TOKEN = f"Token {TOKEN}"
HEADERS = {
    "accept": "application/json",
    "Authorization": AUTH_TOKEN,
    "ACCOUNT-ID": ACCOUNT_ID
}
DEST_FOLDER = DEST_FOLDER

# Optional: Create timestamped folder
# DEST_FOLDER = DEST_FOLDER / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def get_label_from_slug(fields_data, field_slug):
    """Get field label from field slug."""
    for field in fields_data:
        if field['slug'] == field_slug:
            return field['label']
    return None


def get_solutions():
    """Fetch all solutions from SmartSuite API."""
    url = f"{BASE_URL}v1/solutions/"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code != 200:
        print(f'Error loading solutions: {response.status_code}')
        return {}
    
    print('Solutions List Loaded Successfully')
    data = response.json()
    solutions = {}
    
    for solution in data:
        solution_info = {
            'id': solution['id'],
            'name': solution['name'],
            'status': solution['status'],
            'permissions': solution['permissions'],
        }
        solutions[solution['id']] = solution_info
    
    return solutions


def get_tables(solutions):
    """Fetch all tables/applications from SmartSuite API."""
    url = f"{BASE_URL}v1/applications/"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code != 200:
        print(f"Can't load Table list error: {response.status_code}")
        return {}, None
    
    print('Tables List Loaded Successfully')
    tables_data = response.json()
    tables = {}
    teams_table = None
    
    for table in tables_data:
        if table['solution'] in solutions:
            table_info = {
                'id': table['id'],
                'name': table['name'],
                'status': table['status'],
                'solution': solutions[table['solution']]['name'],
                'structure': table['structure'],
                'permissions': table['permissions']
            }
            tables[table['id']] = table_info
            
            # Find the Teams table in System solution
            if (teams_table is None and 
                table['solution'] in solutions and 
                solutions[table['solution']]['name'] == 'System' and 
                table['name'] == 'Teams'):
                teams_table = table['id']
                print('Teams table found')
    
    return tables, teams_table

def get_fields_permission(tables):
    """Fetch field permissions for all tables."""
    field_permissions = []
    
    for table_id, table_info in tables.items():
        table_name = table_info['name']
        solution_name = table_info['solution']
        url = f"{BASE_URL}v1/applications/{table_id}"
        
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print(f"Can't load Table field error: {response.status_code}")
            continue
        
        print(f'Table: {table_id} fields Loaded Successfully')
        fields_data = response.json()
        
        for permission in fields_data['field_permissions']:
            field_info = { 
                'field_slug': permission['field_slug'],
                'solution': solution_name,
                'table': table_name,
                'field': get_label_from_slug(fields_data['structure'], permission['field_slug']),
                'read': permission['read'],
                'write': permission['write']
            }
            if field_info['field'] is not None:
                field_permissions.append(field_info)
    
    return field_permissions

def get_users():
    """Fetch all users from SmartSuite API."""
    url = f"{BASE_URL}v1/applications/members/records/list/"
    response = requests.post(url, headers=HEADERS)
    
    if response.status_code != 200:
        print(f'Error loading users: {response.status_code}')
        return {}
    
    print('Users List Loaded Successfully')
    data = response.json()
    users = {}
    
    for user in data['items']:
        # Skip deleted users and system accounts
        if not user['deleted_date']['date'] and user['type'] != '6':
            user_info = {
                'id': user['id'],
                'full_name': user['full_name']['sys_root'],
                'email': user['email'][0],
                'type': user['type'],
                'role': user['role'],
                'last_login': user['last_login']['date'],
                'locale': user['locale']
            }
            users[user['id']] = user_info
    
    return users

def get_teams(teams_table_id):
    """Fetch all teams from SmartSuite API."""
    if not teams_table_id:
        print('Teams table not found')
        return {}
    
    url = f"{BASE_URL}v1/applications/{teams_table_id}/records/list/"
    params = {"offset": 0}
    response = requests.post(url, params=params, headers=HEADERS)
    
    if response.status_code != 200:
        print(f"Request failed with status code {response.status_code}")
        print(response.text)
        return {}
    
    print('Teams List Loaded Successfully')
    team_data = response.json()
    teams = {}
    
    for team in team_data['items']:
        team_info = {
            'id': team['id'],
            'name': team['name'],
            'status': team['status'],
            'members': team['members']
        }
        teams[team['id']] = team_info
    
    return teams

def recursive_replace_uid(obj, users, teams):
    """Recursively replace user IDs and team IDs with their names."""
    if isinstance(obj, str):
        # Replace user ID with name
        if obj in users:
            if users[obj]['full_name'] == '':
                return users[obj]['email']
            else:
                return users[obj]['full_name']
        
        # Replace team ID with name
        if obj in teams:
            return teams[obj]['name']
        
        return obj
    elif isinstance(obj, dict):
        new_dict = {}
        for key, value in obj.items():
            new_dict[key] = recursive_replace_uid(value, users, teams)
        return new_dict
    elif isinstance(obj, list):
        new_list = []
        for item in obj:
            new_list.append(recursive_replace_uid(item, users, teams))
        return new_list
    else:
        return obj

def write_permissions_to_csv(solutions, tables, field_permissions, users, teams, dest_folder):
    """Write permissions data to CSV file."""
    dest_folder.mkdir(parents=True, exist_ok=True)
    permissions_file = dest_folder / "permissions.csv"
    
    with permissions_file.open("w", newline='', encoding="utf-8-sig") as file:
        csv_writer = csv.writer(file)
        
        # Write headers
        headers = [
            "Type", "Solution", "Table", "Field", "level", "members", "teams",
            "members_read", "members_write", "teams_read", "teams_write",
            "owners", "private_to", "level_read", "level_write"
        ]
        csv_writer.writerow(headers)
        
        # Write solutions
        for solution_id, solution in solutions.items():
            row = [
                "solution",
                solution['name'],
                "",
                "",
                solution['permissions'].get('level', ''),
                solution['permissions'].get('members', ''),
                solution['permissions'].get('teams', ''),
                "",  # members_read
                "",  # members_write
                "",  # teams_read
                "",  # teams_write
                solution['permissions'].get('owners', ''),
                solution['permissions'].get('private_to', ''),
                "",  # level_read
                ""   # level_write
            ]
            csv_writer.writerow(row)
        
        # Write tables
        for table_id, table in tables.items():
            row = [
                "Table",
                table['solution'],
                table['name'],
                "",
                table['permissions'].get('level', ''),
                table['permissions'].get('members', ''),
                table['permissions'].get('teams', ''),
                "",  # members_read
                "",  # members_write
                "",  # teams_read
                "",  # teams_write
                table['permissions'].get('owners', ''),
                table['permissions'].get('private_to', ''),
                "",  # level_read
                ""   # level_write
            ]
            csv_writer.writerow(row)
        
        # Write field permissions
        for field in field_permissions:
            row = [
                "field",
                field['solution'],
                field['table'],
                field['field'],
                "",  # level
                "",  # members
                "",  # teams
                field['read'].get('members', ''),
                field['write'].get('members', ''),
                field['read'].get('teams', ''),
                field['write'].get('teams', ''),
                field.get('owners', ''),
                field.get('private_to', ''),
                field['read']['audience'],
                field['write']['audience']
            ]
            csv_writer.writerow(row)


def main():
    """Main execution function."""
    print("Starting SmartSuite Permissions Audit...")
    
    # Fetch data from API
    solutions = get_solutions()
    if not solutions:
        print("Failed to load solutions. Exiting.")
        return
    
    tables, teams_table_id = get_tables(solutions)
    if not tables:
        print("Failed to load tables. Exiting.")
        return
    
    users = get_users()
    teams = get_teams(teams_table_id)
    
    # Replace IDs with names
    solutions_with_names = recursive_replace_uid(solutions, users, teams)
    tables_with_names = recursive_replace_uid(tables, users, teams)
    
    # Get field permissions
    field_permissions = get_fields_permission(tables)
    field_permissions_with_names = recursive_replace_uid(field_permissions, users, teams)
    
    # Write to CSV
    write_permissions_to_csv(
        solutions_with_names, 
        tables_with_names, 
        field_permissions_with_names, 
        users, 
        teams, 
        DEST_FOLDER
    )
    
    print(f"Permissions audit completed. Results saved to: {DEST_FOLDER / 'permissions.csv'}")


if __name__ == "__main__":
    main()
