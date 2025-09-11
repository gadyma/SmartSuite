#!/usr/bin/env python3
"""
SmartSuite Backup Tool
Version 2.0 - Backup Tables Structure with Attachments

This script backs up SmartSuite data including tables, structures, and attachments.
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
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Import configuration
home_config_path = Path.home() / "config.py"
if home_config_path.exists():
    sys.path.insert(0, str(Path.home()))
    from config import TOKEN, ACCOUNT_ID, DEST_FOLDER
else:
    # Fallback to local config.py if ~/config.py doesn't exist
    from config import TOKEN, ACCOUNT_ID, DEST_FOLDER

# Configuration
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

# Check if a file with the same date already exists in the destination folder
current_date = timestamp.split()[0]
if len(list(Path.home().joinpath(DEST_FOLDER).glob(current_date + '*'))) == 0:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} No existing file found for today. Continuing...")
else:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Exiting program due to existing file for today.")
    sys.exit(0)  # Exit with success code since this is an expected condition

dest_folder = Path.home().joinpath(DEST_FOLDER, timestamp, "")
print(f"Backup destination: {dest_folder}")




# API Configuration
BASE_URL = "https://app.smartsuite.com/api/v1"
AUTH_TOKEN = f"Token {TOKEN}"
HEADERS = {
    "accept": "application/json",
    "Authorization": AUTH_TOKEN,
    "Content-Type": "application/json",
    "ACCOUNT-ID": ACCOUNT_ID
}

def get_solutions():
    """Fetch all solutions from SmartSuite API."""
    response = requests.get(f"{BASE_URL}/solutions/?workspace={ACCOUNT_ID}", headers=HEADERS)
    if response.status_code != 200:
        print(f'Error loading solutions: {response.status_code}')
        return {}
    
    print('Solutions List Loaded Successfully')
    data = response.json()
    solutions = {}
    
    for solution in data:
        solutions[solution['id']] = solution['name']
    
    return solutions

def get_records(application_id, offset=0, limit=1000):
    """Fetch all records from a SmartSuite application."""
    url = f"{BASE_URL}/applications/{application_id}/records/list/"
    payload = {
        "sort": [],
        "filter": {}
    }
    all_records = []
    
    while True:
        params = {
            "offset": offset,
            "limit": limit
        }
        response = requests.post(url, headers=HEADERS, params=params, json=payload)
        response.raise_for_status()
        data = response.json()
        records = data.get('items', [])
        all_records.extend(records)
        
        if len(records) < limit:
            return all_records
        
        offset += limit
    
    return all_records

def download_attachment(url, file_path):
    """Download an attachment from SmartSuite."""
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"File not found: {url}")
        return False
    
    with open(file_path, 'wb') as f:
        f.write(response.content)
        print(f"Downloaded attachment: {url} -> {file_path}")
    
    return True

def get_application_fields(application_id):
    """Get application field structure from SmartSuite API."""
    response = requests.get(f"{BASE_URL}/applications/{application_id}/", headers=HEADERS)
    response.raise_for_status()
    return response.json()

def has_file_field(fields):
    """Check if application has any file fields."""
    return any(field['field_type'] == 'filefield' for field in fields)

def get_file_attachment_fields(fields):
    """Get all file attachment fields from application structure."""
    attachment_fields = {}
    for field in fields['structure']:
        if field['field_type'] == 'filefield':
            attachment_fields[field['slug']] = field['label']
    return attachment_fields

def backup_records_to_csv(records, csv_file):
    """Write records to CSV file."""
    fieldnames = set()
    for record in records:
        fieldnames.update(record.keys())
    
    # Convert the set to a sorted list for consistent column order
    fieldnames = sorted(fieldnames)
    
    with open(csv_file, mode='w', newline='', encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    
    print(f"Data has been written to {csv_file}")

solutions = get_solutions()

def get_application(solution_id):
    """Get applications for a specific solution."""
    response = requests.get(f"{BASE_URL}/applications/?solution={solution_id}", headers=HEADERS)
    if response.status_code != 200:
        print(f"Can't load Table list error: {response.status_code}")
        return None
    
    print('Tables List Loaded Successfully')
    return response.json()

def get_applications():
    """Get all applications from SmartSuite API."""
    response = requests.get(f"{BASE_URL}/applications/", headers=HEADERS)
    if response.status_code != 200:
        print(f"Can't load Table list error: {response.status_code}")
        return None
    
    print('Tables List Loaded Successfully')
    return response.json()


def delete_old_backups(days):
    """Delete backup folders older than specified days."""
    main_folder = os.path.expanduser(DEST_FOLDER)
    current_time = time.time()
    cut_off_time = current_time - (days * 86400)  # Convert days to seconds
    
    if not os.path.exists(main_folder):
        print(f'The directory {main_folder} does not exist.')
        return
    
    for folder_name in os.listdir(main_folder):
        folder_path = os.path.join(main_folder, folder_name)
        
        if os.path.isdir(folder_path):
            modification_time = os.path.getmtime(folder_path)
            
            if modification_time < cut_off_time:
                print(f'Deleting old backup folder: {folder_path}')
                shutil.rmtree(folder_path)

def main():
    """Main execution function."""
    print("Starting SmartSuite Backup...")
    
    # Get solutions and applications
    solutions = get_solutions()
    if not solutions:
        print("Failed to load solutions. Exiting.")
        return
    
    tables_data = get_applications()
    if not tables_data:
        print("Failed to load applications. Exiting.")
        return
    
    # Process each table
    for table in tables_data:
        app_name = table['name']
        app_id = table['id']
        app_status = table['status']
        app_solution = table['solution']
        table_structure = table['structure']
        
        # Get solution name
        if app_solution in solutions:
            app_solution_name = solutions[app_solution]
        else:
            app_solution_name = app_solution
        
        # Skip test solutions
        if app_solution_name == app_solution or app_solution_name.startswith('תTest'):
            continue
        
        print(f'Processing: {app_solution_name} -> {app_name} (ID: {app_id}, Status: {app_status})')
        # Prepare fields for CSV export
        fields = []
        for field in table['structure']:
            fields.append(field['slug'])
        
        # Remove system fields
        system_fields = ["followed_by", "autonumber"]
        for field in system_fields:
            if field in fields:
                fields.remove(field)
        
        # Generate CSV backup
        url_csv = f"{BASE_URL}/applications/{app_id}/records/generate_csv/"
        json_query = {'visible_fields': fields}
        response = requests.post(url_csv, headers=HEADERS, json=json_query)
        
        if response.status_code != 200:
            print(f'Error generating CSV: {response.status_code}')
            continue
        
        # Create solution folder
        sol_folder = dest_folder / app_solution_name.replace("/", "_")
        sol_folder.mkdir(parents=True, exist_ok=True)
        
        # Write CSV file
        csv_file = sol_folder / f"{app_name.replace('/', '_')}.csv"
        with open(csv_file, "w", encoding="utf-8") as f:
            f.write(response.content.decode('UTF-8'))
        
        print(f"  -> CSV backup created: {csv_file}")
        # Backup structure
        sol_path = dest_folder / app_solution_name.replace("/", "_")
        sol_path.mkdir(parents=True, exist_ok=True)
        
        structure_file = sol_path / f"{app_name.replace('/', '_')}.structure.json"
        with open(structure_file, "w", encoding="utf-8") as f:
            json.dump(table_structure, f, indent=2)
        
        print(f"  -> Structure backup created: {structure_file}")
        # Get records for attachment backup
        records = get_records(app_id)
        
        # Backup attachments
        fields = get_application_fields(app_id)
        if not has_file_field(fields['structure']):
            print(f"  -> No file fields found, skipping attachments")
            continue
        
        file_att_fields = get_file_attachment_fields(fields)
        attachment_count = 0
        
        for record in records:
            for slug, label in file_att_fields.items():
                if slug in record and record[slug]:
                    for attachment in record[slug]:
                        file_handle = attachment['handle']
                        file_name = attachment['metadata']['filename']
                        file_url = f"{BASE_URL}/shared-files/{file_handle}/get_url/"
                        
                        # Create directory structure
                        dir_path = sol_path / f"{app_name}/Attachments/{record['id']}"
                        dir_path.mkdir(parents=True, exist_ok=True)
                        
                        # Download attachment
                        file_path = dir_path / file_name
                        if download_attachment(file_url, str(file_path)):
                            attachment_count += 1
        
        if attachment_count > 0:
            print(f"  -> Downloaded {attachment_count} attachments")
    # Clean up old backups (older than 8 days)
    delete_old_backups(8)
    
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Backup completed successfully!")


if __name__ == "__main__":
    main()