##intallation prerequisites:
#python3 -m pip install requests
#Version 1.01
import requests
import json
import os

#Secrets

"""
You should do it in a more secure way than hard code :) 
But this is a smaple code I wrote
Replace the Token & ACCOUNT-ID with your own.
"""
# Generating an API Key - https://help.smartsuite.com/en/articles/4855681-generating-an-api-key
token="PlaceYourToken"
ACCOUNT_ID="PlaceyourAccoutID"

# What folder to to write the CSV?
destFolder="/temp/backup/"

###Did you put your details above?

#Param
baseURL = "https://app.smartsuite.com/api/"
tk="Token " + token
# the account id you take the first part in the URL https://app.smartsuite.com/ACCOUNTID/solution/SOLUTIONID
headers = {"accept":"application/json","Authorization":str(tk),"ACCOUNT-ID":ACCOUNT_ID}
urlApplications=baseURL + "v1/applications/"


def getsolutions():
   urlS=baseURL + "v1/solutions/"
   respS= requests.get(urlS, headers=headers)
   if respS.status_code != 200:
      print('error: ' + str(respS.status_code))
   else:
      print('Solutions List Loaded Successfully')
      dataS=respS.json()
   solutions={}
   solutions.clear
   for s in dataS:
     #print(s['name'],s['id'])
     solutions[s['id']]=s['name']
   return solutions



solutions=getsolutions()

resp= requests.get(urlApplications, headers=headers)

if resp.status_code != 200:
   print("Can't load Table list error: " + str(resp.status_code))
else:
   print('Tables List Loaded Successfully')
   tablesdata=resp.json()

for table in tablesdata:
  appName = table['name']
  appID = table['id']
  appStatus = table['status']
  appsolution = table['solution']
  if appsolution in solutions:
    appSolutionname=solutions[appsolution]
  else:
     appSolutionname=appsolution
  #If you want to exclude things from backup...
  # if appsolution!="Something":
  #   continue    
 
  print(f'solu: {appsolution} : {appSolutionname}, Appid: {appID}, appStatus: {appStatus}, TableName : {appName}')
  fields=[]
  fieldsNames=[]
  for field in table['structure']: 
     fields.append(field['slug'])
     fieldsNames.append(field['label'])
  #print(fields)
  #print(fieldsNames)
  if "followed_by" in fields: fields.remove("followed_by")
  if "autonumber" in fields: fields.remove("autonumber")
  if "Followed By" in fieldsNames: fieldsNames.remove("Followed By")
  if "Open Comments" in fieldsNames: fieldsNames.remove("Open Comments") 
  if "Auto Number" in fieldsNames: fieldsNames.remove("Auto Number") 
  urlcsv=baseURL + "v1/applications/" + appID+"/records/generate_csv/"
  jsonqry={'visible_fields':fields}
  resp2= requests.post(urlcsv, headers=headers,json=jsonqry)
  if resp2.status_code != 200:
     print('error: ' + str(resp2.status_code))
  else:
     solFolder=destFolder + appSolutionname.replace("/","_") +'/'
     #print("working on " + solFolder)
     if not os.path.exists(solFolder): 
        os.makedirs(solFolder) 
     f = open(solFolder +'/' + appName.replace("/","_") +".csv", "w", encoding="utf-8")
     f.write(resp2.text)
     f.close()


