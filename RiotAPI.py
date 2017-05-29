import RiotConstants as Consts
import requests
import pymysql
import numpy as np
import json
import pandas as pd
import time
import random
from warnings import filterwarnings
filterwarnings('ignore', category = pymysql.Warning)
import matplotlib.pyplot as plt 
import seaborn as sns 

class RiotAPI(object):

	def __init__(self, api_key, region=Consts.REGIONS['north_america']):
		self.api_key = api_key
		self.region = region

	#Define the decorator for the request function that limits the rate of API queries
	def RateLimited(maxPerSecond):
		minInterval = 1.0 / float(maxPerSecond)
		def decorate(func):
			lastTimeCalled = [0.0]
			def rateLimitedFunction(*args,**kargs):
				elapsed = time.clock() - lastTimeCalled[0]
				leftToWait = minInterval - elapsed
				if leftToWait>0:
					time.sleep(leftToWait)
				ret = func(*args,**kargs)
				lastTimeCalled[0] = time.clock()
				return ret
			return rateLimitedFunction
		return decorate

	#Implement the decorator on _request 
	@RateLimited(500/10/60)
	def _request(self,api_url, params={}):
		args = {'api_key':self.api_key}
		for key,value in params.items():
			if key not in args:
				args[key] = value
		response = requests.get(
			Consts.URL['base'].format(
				proxy=self.region,
				region=self.region,
				url=api_url
				),
			params=args
			)
		return response

	def get_summoner_by_name(self,name):
		api_url = Consts.URL['summoner_by_name'].format(
			version=Consts.API_VERSIONS['summoner'],
			names = name
			)
		return self._request(api_url).json()

	def get_summoner_matches_by_id(self,accountID):
		api_url = Consts.URL['match_list'].format(
			version=Consts.API_VERSIONS['summoner'],
			accountId=accountID
			)
		return self._request(api_url).json()

	def get_game_ids_by_name(self,name):
		gameIDs = []
		AccountID = self.get_summoner_by_name(name)['accountId']
		matchJSON = self.get_summoner_matches_by_id(AccountID)['matches']
		for item in matchJSON:
			gameIDs.append(item['gameId'])

		return gameIDs

	def write_summoner_to_db(self,name):
		#Conect to DB
		
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='YOU WISH', db='league_data')
		cur = conn.cursor()

		#This stuff was important before I learned about INSERT IGNORE.  I'm keeping it here for future ref.
		#sql1 = "SELECT name from summoners where name=%s"
		#n = cur.execute(sql1, (name))

		#Get JSON data
		summoner = self.get_summoner_by_name(name)
		#Construct the insert statement (Table summoners has a primary key built into all columns to prevent duplicates)
		sql = "INSERT IGNORE INTO summoners (name, id_no, accountId, revisionDate) VALUES (%s,%s,%s,%s)"
		#Execute the insert
		cur.execute(sql, (name, summoner['id'], summoner['accountId'], summoner['revisionDate'])) 
		conn.commit()
		
		#Close the connection
		cur.close()
		conn.close()

	def get_warding_data_histogram(self, accountID, matchID):
		#This function displays a histogram of wards placed throughout a match, together with important objective timers.

		#Need to search all of the events in a given match for wards created by the given summoner
			#Match summonerId to creatorId
			#Search all instances of events in the timeline JSON file for type="WARD_PLACED" by "creatorId='summoner' "
		#Once all the instances of a given summoner creating a ward have been gathered, we place them on a histogram in 2 minute bins

		creatorId = 0
		
		#URLS to request:
		#Match information
		api_url1 = Consts.URL['match_info'].format(
			version=Consts.API_VERSIONS['summoner'],
			matchId=matchID
			) 
		#Timeline information
		api_url2 = Consts.URL['timeline'].format(
			version=Consts.API_VERSIONS['summoner'],
			matchId=matchID
			)

		#Send the API requests to get the match information and the timeline data
		match_api = self._request(api_url1)
		matchJSON = match_api.json()
		timeline_api = self._request(api_url2)
		timelineJSON = timeline_api.json()
		
		#Match the participantID to the accountID by searching for the proper accountID in the JSON data
		for i in matchJSON['participantIdentities']:
			if i['player']['accountId'] == accountID:
				creatorId = i['participantId']
				print(i['player'])
				break

		#Initialize lists for storing ward time stamps and corresponding types
		time_stamps = []
		ward_types = []

		#Initialize lists for elite monster spawn times.  We will only track first spawns, as well as monsters that were killed in game.  Since only one riftherald spawns, we just write down its spawn time

		elite_monster_spawn_times = []
		drag_spawn = 2.5
		baron_spawn = 20

		elite_monster_death_times = []
		elite_monster_type = []

		#Search through the timeline events for ward placements and dragon/baron/herald kills
		for frame in timelineJSON['frames']:
			for event in frame['events']:
				if event['type'] == 'WARD_PLACED' and event['creatorId']==creatorId:
					time_stamps.append(event['timestamp'])
					ward_types.append(event['wardType'])
				#If the event is an elite monster kill,	
				elif event['type'] == 'ELITE_MONSTER_KILL':
					#Check whether it is dragon or baron
					if event['monsterType'] == 'DRAGON':
						elite_monster_type.append(event['monsterSubType'])
						elite_monster_death_times.append(event['timestamp'])
						elite_monster_spawn_times.append(drag_spawn)

						#update the dragon spawn time according to the last death time
						drag_spawn = event['timestamp']/1000/60 + 6
					elif event['monsterType'] == 'BARON_NASHOR':
						elite_monster_type.append(event['monsterType'])
						elite_monster_death_times.append(event['timestamp'])
						elite_monster_spawn_times.append(baron_spawn)

						#update baron spawn time according to the last death
						baron_spawn = event['timestamp']/1000/60 + 7
					elif event['monsterType'] == 'RIFTHERALD':
						elite_monster_type.append(event['monsterType'])
						elite_monster_death_times.append(event['timestamp'])
						elite_monster_spawn_times.append(10)

			#Scan the list of monsters that were killed in the match, and if Baron or Herald were not killed, include them in the monsters that spawned.
			#By convention, a death time of 1000 means they were not killed.
			if 'BARON_NASHOR' not in elite_monster_type:
				elite_monster_type.append('BARON_NASHOR')
				elite_monster_spawn_times.append(20)
				elite_monster_death_times.append(1000)
			elif 'RIFTHERALD' not in elite_monster_type:
				elite_monster_type.append('RIFTHERALD')
				elite_monster_spawn_times.append(10)
				elite_monster_death_times.append(1000)



		#Retrieve the last time a ward was placed (in minutes) by creatorId
		last_time = time_stamps[len(time_stamps)-1]/1000/60

		#Store the data in a pandas DataFrame so that I can group by ward type (control, yellow, sightstone, etc.)
		ward_df = pd.DataFrame({'Time Stamp': time_stamps, 'Ward Type': ward_types})
		ward_by_type_data = [j[1].iloc[:,0].values/1000/60 for j in ward_df.groupby('Ward Type')]
		ward_by_type_type = [j[1].iloc[0,1] for j in ward_df.groupby('Ward Type')]

		#Use 2 minute bins
		numBins = int(-(-last_time//1))

		#Assign colours to the different monsters:
		monster_colours = {'BARON_NASHOR':'purple', 'EARTH_DRAGON':'brown', 'AIR_DRAGON':'grey', 'FIRE_DRAGON':'red', 'WATER_DRAGON':'blue', 'ELDER_DRAGON':'yellow','RIFTHERALD':'cyan'}
		ward_colours = {'YELLOW_TRINKET':'yellow', 'SIGHT_WARD':'green', 'CONTROL_WARD':'red', 'BLUE_TRINKET':'blue'}
		#Build the warding data plot for creatorID, together with objective information
		plt.figure()
		for i in range(0,len(elite_monster_type)):
			#Check which monster spawned and add a vertical line to the plot at the appropriate time with the corresponding colour
			t = elite_monster_spawn_times[i]
			C = monster_colours[elite_monster_type[i]]
			plt.axvline(t, color=C, alpha=0.6)

		#Match the list of ward types with their corresponding colours using the appropriate dictionary
		clist = [ward_colours[w] for w in ward_by_type_type]
		#Plot the ward placement histogram
		plt.hist(ward_by_type_data,stacked=True, color=clist, alpha=0.7, bins=numBins)

		plt.show()

	def populate_summoners_from_seed(self,file_input):
		#file_input should be a string which points to the file directory containing the match seed data provided at:
			#https://s3-us-west-1.amazonaws.com/riot-developer-portal/seed-data/matches10.json OR
			#https://s3-us-west-1.amazonaws.com/riot-developer-portal/seed-data/matches1.json
		with open(file_input,encoding='utf-8') as seed_json:
			#Load in the JSON data
			seed = json.load(seed_json)
	
			#Open the connection to the mysql server
			conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='YOU WISH', db='league_data')
			cur = conn.cursor()

			#For each match in the database, record the summoner information of the participants, together with the corresponding relevant match information
			for match in seed['matches']:
				#Insert the data into the summoners table
				for participant in match['participantIdentities']:
					summoners_sql = "INSERT IGNORE INTO summoners (summonerId, accountId, username, revisionDate) VALUES (%s,%s,%s,%s)"
					
					#There is a stupid formatting error in the data, whereby a players accountId is encoded in their match history from the 29th character onwards, or the 28th
					#depending on whether or not the server that stores that information is labelled as NA1 or NA.  We need to account for this.
					if participant['player']['matchHistoryUri'][0:28] == '/v1/stats/player_history/NA1':
						accId = int(participant['player']['matchHistoryUri'][29:])
					elif participant['player']['matchHistoryUri'][0:28] == '/v1/stats/player_history/NA/':
						accId = int(participant['player']['matchHistoryUri'][28:])
					else:
						continue

					#Execute and commit the insert query	
					cur.execute(summoners_sql, (participant['player']['summonerId'], accId, participant['player']['summonerName'], match['matchCreation'])) 
					conn.commit()

				

			#Close the connection
			cur.close()
			conn.close()						
	


	def populate_matches_from_summoners(self, sumNo, matchNo):
	#This function is a spider that crawls the riot servers to populate our MySQL server.  It works by:
		#1. Select approximately sumNo summoners at random from the summoners table
			#This turnes out to be surprisingly tricky when the number of entries in the summoners table gets large. See http://www.rndblog.com/how-to-select-random-rows-in-mysql/ for an explanation.
		#2. Pick out matchNo of each of their matches, again at random
		#3	a) Insert the summoners from those matches into the summoners table
		#	b) Add the match to the matches table
		#	c) Update the junction table

		#STEP 1: {

		#Connect to the DB
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='YOU WISH', db='league_data', charset='utf8')
		cur = conn.cursor()

		#Find the number of summoners in our table, fix a desired number of summoners to select, then figure out the threshhold probability 
		query_result = cur.execute('SELECT COUNT(*) FROM summoners')
		NumSumRows = float(cur.fetchone()[0])
		P = sumNo/NumSumRows

		#Select sumNo rows from summoners at random
		sql = 'SELECT accountId FROM summoners WHERE RAND() <= ' +  str(P)
		accountIds = cur.execute(sql)
		L = cur.fetchall()

		print('We are going to fetch the records from ' + str(len(L)) + ' summoners! Beginning now.')
		# }

		#STEP 2: {
		count = 0
		for i in L:
			if count % 100 == 0:
				print('Progress: ' + str(count) + ' records')
			count+=1
			accountId = i[0]
			matchJSON = self.get_summoner_matches_by_id(accountId)
			
			#First throw away any matches played before season 6
			tempM = [x for x in matchJSON['matches'] if x['season'] >= 6]

			#I need to select matchNo of these entries at random.
			
			sumMatchNo = len(tempM)
			#If there are no matches played after season 6 then I'll get a divide by zero error
			if sumMatchNo == 0:
				continue

			matchProb = min(matchNo, sumMatchNo)/sumMatchNo
			#Now select approximately matchNo matches from the list of matches the summoner has played since season 6
			M = [x for x in tempM if random.random() <= matchProb]

			for match in M:	
				#Match information
				api_url1 = Consts.URL['match_info'].format(
					version=Consts.API_VERSIONS['summoner'],
					matchId=match['gameId']
					) 
				api_query = self._request(api_url1)
				gameJSON = api_query.json()
				
				if api_query.status_code == 200:
					#Check that the match was played on Summoner's rift, mapId = 11
					if gameJSON['mapId'] != 11:
						#Move to the next match if it is a game other than Summoner's rift.
						continue

					#Update the summoners table with new entries from this game
					for participant in gameJSON['participantIdentities']:
						summoners_sql = "INSERT IGNORE INTO summoners (summonerId, accountId, username) VALUES (%s,%s,%s)"
						#Execute and commit the insert query	
						cur.execute(summoners_sql, (participant['player']['summonerId'], participant['player']['accountId'], participant['player']['summonerName'])) 
						conn.commit()

					match_sql = "INSERT IGNORE INTO matches (matchId, duration, season, version, firstDrag, firstBaron, herald, firstInhib, firstTurret, firstBlood, redDrags, redBarons, redTowers, redInhibs, blueDrags, blueBarons, blueTowers, blueInhibs, win) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
					#Build the insert query for the match table

					#Determine who got first drag, baron, herald, etc.  We use the convention that team 200 := True and team 100 := False, while neither := None
					if gameJSON['teams'][1]['firstBaron'] == True:
						fB = True 
					elif gameJSON['teams'][0]['firstBaron'] == True:
						fB = False
					else:
						fB = None
					if gameJSON['teams'][1]['firstDragon'] == True:
						fD = True
					elif gameJSON['teams'][0]['firstDragon'] == True:
						fD = False
					else:
						fD = None
					if gameJSON['teams'][1]['firstRiftHerald'] == True:
						H = True
					elif gameJSON['teams'][0]['firstRiftHerald'] == True:
						H = False
					else:
						H = None
	
					#Determine who won the game
					#If team 200 won the game
					if  gameJSON['teams'][1]['win'] == 'Win':
						W = True
					#If not, then team 100 won
					else:
						W = False

					cur.execute(match_sql, (gameJSON['gameId'], gameJSON['gameDuration'], gameJSON['seasonId'], gameJSON['gameVersion'], fD, fB, H, gameJSON['teams'][1]['firstInhibitor'], gameJSON['teams'][1]['firstTower'], gameJSON['teams'][1]['firstBlood'], gameJSON['teams'][0]['dragonKills'], gameJSON['teams'][0]['baronKills'], gameJSON['teams'][0]['towerKills'], gameJSON['teams'][0]['inhibitorKills'], gameJSON['teams'][1]['dragonKills'], gameJSON['teams'][1]['baronKills'], gameJSON['teams'][1]['towerKills'], gameJSON['teams'][1]['inhibitorKills'], W))
					conn.commit()
					
					for participant in gameJSON['participantIdentities']:
						#Update the junction table
						summonerjctmatch_sql = "INSERT IGNORE INTO summonersjctmatches (summonerId, matchId, champId, team, lane, role, tier) VALUES (%s, %s,%s,%s,%s,%s,%s)"
						participantId = participant['participantId']-1
						
						cur.execute(summonerjctmatch_sql, (participant['player']['summonerId'], match['gameId'], match['champion'], gameJSON['participants'][participantId]['teamId'], match['lane'],match['role'], gameJSON['participants'][participantId]['highestAchievedSeasonTier']))
						conn.commit()

				
				#If the API request code is anything but a 200 then just move along to the next query
				else:
					continue	

		# }

#		print(accoundIds)
	
#I had originally written this method when I had thought there was some problem with the code that recorded summoners into the junction table.  I was finding that there were fewer
#than 10 summoners per match. The problem was that I forgot to only record data from matches played on summoners rift (whoops).  I am keeping this function in case I need it
#at some point in the future.
	def validate_matches_table(self):
		offset = 300;
		#This function goes through the whole matches table and makes sure that all the summoners who played in a match are recorded in summonersjctmatches.  If for some reason (connection error, etc.) a summoner is not recorded, this will fix it.
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='YOU WISH', db='league_data', charset='utf8')
		cur = conn.cursor()	
		count_query = 'SELECT count(*) FROM matches'
		cur.execute(count_query)
		match_count = cur.fetchone()[0]
		print(match_count)

		while offset < match_count: #replace w match_count
			matches_query = 'SELECT matchId FROM matches LIMIT 100 OFFSET ' + str(offset)
			cur.execute(matches_query)
			matchIds = cur.fetchall()

			for row in matchIds:
				Id = row[0]
				#For each match, check that there are 10 recorded summoners.
				cur.execute('SELECT summonerId FROM summonersjctmatches WHERE matchId = ' + str(Id))
				results = cur.fetchall()
				if len(results) == 10:
					#We have found 10 summoners recorded for the given match Id, as expected.  Go to the next row
					continue
				else:
					#Otherwise, we found a match with fewer than 10 summoners recorded. Find the missing one and record their info.

					#Indicate the match with the record issue
					print('There was a problem with match ' + str(Id) + '.  Correcting the issue now...')
					
					#Make a set containing all the recorded summonerIds for this match
					set_of_records = set()
					for rows in results:
						set_of_records.add(rows[0])
					print(set_of_records) 
					#Now make a set containing all the summonerIds from the match data
					api_url1 = Consts.URL['match_info'].format(
						version=Consts.API_VERSIONS['summoner'],
						matchId=Id
						) 
					api_query = self._request(api_url1)
					if api_query.status_code != 200:
						print('There was a problem.  API returned status code ' + str(api_query.status_code))
						return

					gameJSON = api_query.json()

					set_of_sumIds = set()
					for participant in gameJSON['participantIdentities']:
						print(gameJSON['participantIdentities'])
						#If the participant is not recorded, add them to our records
						if participant['player']['summonerId'] not in set_of_records:
							print('Found missing summoner! Adding '+participant['player']['summonerId']+' to match number ' + str(Id))
							summonerjctmatch_sql = "INSERT IGNORE INTO summonersjctmatches (summonerId, matchId, champId, team, lane, role, tier) VALUES (%s, %s,%s,%s,%s,%s,%s)"
							participantId = gameJSON['participantIdentities']['participantId']-1
							cur.execute(summonerjctmatch_sql, (participant['summonerId'], Id, gameJSON['participants'][participantId]['championId'], gameJSON['participants'][participantId]['teamId'], gameJSON['participants'][participantId]['timeline']['lane'] , gameJSON['participants'][participantId]['timeline']['role'], gameJSON['participants'][participantId]['highestAchievedSeasonTier']))
							conn.commit()
						else:
							continue
			offset += 100
			print(offset)




#Coming soon!

	#def win_prob_with_objective_by_tier(self,objective,tier):
	#This function queries our match database to determine what is the probability of winning the game if you get first drag, baron, tower, or blood.
	#objective is a string with values: 'Dragon', 'Baron', 'Blood', or 'Tower'
	#tier is a string with values: 'Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond', 'Master', 'Challenger'

	