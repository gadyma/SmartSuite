# Installation prerequisites:
# python3 -m pip install requests
# Version 2.0 - Backup Tables Structure with Attachments
import sys
import requests
import json
import os
from datetime import datetime
from pathlib import Path
import csv
import time
import shutil

# Secrets
from config import TOKEN, ACCOUNT_ID,DEST_FOLDER

# Generating an API Key - https://help.smartsuite.com/en/articles/4855681-generating-an-api-key
# Create a config.py file and put in it the TOKEN &  ACCOUNT_ID

# What folder to write the CSV?
# destFolder="/Users/gadymargalit/backup/"
# destFolder="/temp/backup/"

timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")


#Check if a file with the same date already exists in the destination folder.
current_date = timestamp.split()[0]
if len(list(Path.home().joinpath(DEST_FOLDER).glob(current_date+'*'))) ==0:
    print("No existing file found for today. Continuing...")
    # Your program continues here
else:
    print("Exiting program due to existing file for today.")
    sys.exit(0)  # Exit with success code since this is an expected condition


dest_folder = Path.home().joinpath(DEST_FOLDER,timestamp, "")
print(dest_folder)




# Did you put your details above?
# Param
BASE_URL = "https://app.smartsuite.com/api/v1"
tk = "Token " + TOKEN
# The account id you take the first part in the URL https://app.smartsuite.com/ACCOUNTID/solution/SOLUTIONID
headers = {
    "accept": "application/json",
    "Authorization": str(tk),
    "Content-Type": "application/json",
    "ACCOUNT-ID": ACCOUNT_ID
    }

def get_solutions():
    response = requests.get(f"{BASE_URL}/solutions/?workspace={ACCOUNT_ID}", headers=headers)
    if response.status_code != 200:
        print('error: ' + str(response.status_code))
    else:
        print('Solutions List Loaded Successfully')
        data_s = response.json()
    solutions = {}
    solutions.clear()
    for s in data_s:
        # print(s['name'],s['id'])
        solutions[s['id']] = s['name']
    return solutions

def get_records(application_id, offset=0, limit=1000):
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
        response = requests.post(url, headers=headers, params=params, json=payload)
        response.raise_for_status()
        data = response.json()
        records = data.get('items', [])
        all_records.extend(records)
        if len(records)<limit:
            return all_records
        offset += limit
    return all_records

def download_attachment(url, file_path):
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"File not found {url}")
    else:
        with open(file_path, 'wb') as f:
            f.write(response.content)

def get_application_fields(application_id):
    response = requests.get(f"{BASE_URL}/applications/{application_id}/", headers=headers)
    response.raise_for_status()
    return response.json()

def has_file_field(fields):
    return any(field['field_type'] == 'filefield' for field in fields)

def file_attchment_fields(fields):
    Attachment_fields={}
    for field in fields['structure']:
        if field['field_type'] != 'filefield':
            continue
        Attachment_fields[field['slug']]=field['label']
    return Attachment_fields

def backup_records_new(records,csv_file): # need debug
    # Writing to CSV
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
    response = requests.get(f"{BASE_URL}/applications/?solution={solution_id}", headers=headers)
    if response.status_code != 200:
        print("Can't load Table list error: " + str(response.status_code))
    else:
        print('Tables List Loaded Successfully')
    return response.json()

def get_applications():
    response = requests.get(f"{BASE_URL}/applications/", headers=headers)
    if response.status_code != 200:
        print("Can't load Table list error: " + str(response.status_code))
    else:
        print('Tables List Loaded Successfully')
    return response.json()


def del_old(days):
    # Path to the main folder
    main_folder = DEST_FOLDER
    main_folder = os.path.expanduser(main_folder)
    # Get the current time
    current_time = time.time()
    # Set the cut-off time to 7 days ago
    cut_off_time = current_time - (days * 86400)  # 7 days in seconds
    # Check if the main folder exists
    if os.path.exists(main_folder):
        # Iterate through all items in the main folder
        for folder_name in os.listdir(main_folder):
            folder_path = os.path.join(main_folder, folder_name)
            # Check if it's a directory
            if os.path.isdir(folder_path):
                # Get the modification time of the folder
                modification_time = os.path.getmtime(folder_path)
                # If the folder is older than 7 days, delete it
                if modification_time < cut_off_time:
                    print(f'Deleting folder: {folder_path}')
                    shutil.rmtree(folder_path)
    else:
        print(f'The directory {main_folder} does not exist.')

tables_data = get_applications()

for table in tables_data:
    app_name = table['name']
    app_id = table['id']
    app_status = table['status']
    app_solution = table['solution']
    table_structure = table['structure']
    if app_solution in solutions:
        app_solution_name = solutions[app_solution]
    else:
        app_solution_name = app_solution
    # If you want to exclude things from backup...
    if app_solution_name == app_solution or app_solution_name.startswith('תTest'):
        continue
    print(f'solu: {app_solution} : {app_solution_name}, Appid: {app_id}, appStatus: {app_status}, TableName : {app_name}')
    #old Backup
    fields = []
    fields_names = []
    for field in table['structure']:
        fields.append(field['slug'])
        fields_names.append(field['label'])
    # print(fields)
    # print(fieldsNames)
    if "followed_by" in fields:
        fields.remove("followed_by")
    if "autonumber" in fields:
        fields.remove("autonumber")
    if "Followed By" in fields_names:
        fields_names.remove("Followed By")
    if "Open Comments" in fields_names:
        fields_names.remove("Open Comments")
    if "Auto Number" in fields_names:
        fields_names.remove("Auto Number")
    # generate CSV to backup
    url_csv = BASE_URL + "/applications/" + app_id + "/records/generate_csv/"
    json_qry = {'visible_fields': fields}
    resp2 = requests.post(url_csv, headers=headers, json=json_qry)
    resp2.content.decode('UTF-8')
    if resp2.status_code != 200:
        print('error: ' + str(resp2.status_code))
    else:
        sol_folder = dest_folder / app_solution_name.replace("/", "_") 
        # print("working on " + solFolder)
        if not os.path.exists(sol_folder):
            os.makedirs(sol_folder)
        f = open(sol_folder / (app_name.replace("/", "_") + ".csv"), "w", encoding="utf-8")
        # f.write(resp2.text)
        f.write(resp2.content.decode('UTF-8'))
        f.close()
    # BackupStructure
    sol_path = dest_folder / app_solution_name.replace("/", "_") 
    os.makedirs(sol_path, exist_ok=True)
    f = open(sol_path / (app_name.replace("/", "_") + ".structure.json"), "w", encoding="utf-8")
    f.write(json.dumps(table_structure))
    # f.tableStructure, f, indent=2)
    f.close()
    records = get_records(app_id)
    #Backup Records
    #backup_records_new(records,sol_path / f"{app_name}.csv")
    #backup Attachments
    # Backup attackments
    fields = get_application_fields(app_id)
    if not has_file_field(fields['structure']):
        #print(f"Skipping application '{app_name}' as it has no file fields.")
        continue
    file_att_fields=file_attchment_fields(fields)
    for record in records:
        for (slug,label) in file_att_fields.items():
            if slug in record and record[slug]:
                for attachment in record[slug]:
                    file_handle= attachment['handle']
                    #if isinstance(field_value, list) and field_value and isinstance(field_value[0], dict) and 'url' in field_value[0]:
                    file_name = attachment['metadata']['filename']
                    file_url = f"{BASE_URL}/shared-files/{file_handle}/get_url/"
                    # Create directory structure
                    dir_path = sol_path / f"{app_name}/Attachments/{record['id']}"
                    os.makedirs(dir_path, exist_ok=True)
                    # Download attachment
                    file_path = f"{dir_path}/{file_name}"
                    download_attachment(file_url, file_path)
    
        
del_old(8)
