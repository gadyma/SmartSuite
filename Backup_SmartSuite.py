# Installation prerequisites:
# python3 -m pip install requests
# Version 1.2 - Backup Tables Structure
import requests
import json
import os
from datetime import datetime
from pathlib import Path

# Secrets
from config import TOKEN, ACCOUNT_ID

# Generating an API Key - https://help.smartsuite.com/en/articles/4855681-generating-an-api-key
# Create a config.py file and put in it the TOKEN &  ACCOUNT_ID

# What folder to write the CSV?
destFolder="backup"
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
dest_folder = Path.home().joinpath(dest_folder,timestamp, "")
print(dest_folder)

# Did you put your details above?
# Param
base_url = "https://app.smartsuite.com/api/"
tk = "Token " + TOKEN
# The account id you take the first part in the URL https://app.smartsuite.com/ACCOUNTID/solution/SOLUTIONID
headers = {"accept": "application/json", "Authorization": str(tk), "ACCOUNT-ID": ACCOUNT_ID}
url_applications = base_url + "v1/applications/"



def get_solutions():
    url_s = base_url + "v1/solutions/"
    resp_s = requests.get(url_s, headers=headers)
    if resp_s.status_code != 200:
        print('error: ' + str(resp_s.status_code))
    else:
        print('Solutions List Loaded Successfully')
        data_s = resp_s.json()
    solutions = {}
    solutions.clear()
    for s in data_s:
        # print(s['name'],s['id'])
        solutions[s['id']] = s['name']
    return solutions


solutions = get_solutions()
resp = requests.get(url_applications, headers=headers)
if resp.status_code != 200:
    print("Can't load Table list error: " + str(resp.status_code))
else:
    print('Tables List Loaded Successfully')
    tables_data = resp.json()

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

    url_csv = base_url + "v1/applications/" + app_id + "/records/generate_csv/"
    json_qry = {'visible_fields': fields}
    resp2 = requests.post(url_csv, headers=headers, json=json_qry)
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
    f = open(sol_folder / (app_name.replace("/", "_") + ".structure.json"), "w", encoding="utf-8")
    f.write(json.dumps(table_structure))
    # f.tableStructure, f, indent=2)
    f.close()
