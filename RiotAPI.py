import RiotConstants as Consts
import ChampStaticData as ChampData
import tensorflow as tf
import numpy as np
import requests
import pymysql
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
		return self._request(api_url)

	def get_game_ids_by_name(self,name):
		gameIDs = []
		AccountID = self.get_summoner_by_name(name)['accountId']
		matchJSON = self.get_summoner_matches_by_id(AccountID)['matches']
		for item in matchJSON:
			gameIDs.append(item['gameId'])

		return gameIDs

	def write_summoner_to_db(self,name):
		#Conect to DB
		
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data')
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
			conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data')
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
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data', charset='utf8')
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
			match_api_response = self.get_summoner_matches_by_id(accountId)
			
			#Go to the next summoner if the match list response code is not 200
			if match_api_response.status_code != 200:
				continue
			else:
				matchJSON = match_api_response.json()

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
						cur.execute(summonerjctmatch_sql, (participant['player']['summonerId'], gameJSON['gameId'], gameJSON['participants'][participantId]['championId'], gameJSON['participants'][participantId]['teamId'], gameJSON['participants'][participantId]['timeline']['lane'] , gameJSON['participants'][participantId]['timeline']['role'], gameJSON['participants'][participantId]['highestAchievedSeasonTier']))
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
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data', charset='utf8')
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



	def win_probability_with_objective_by_tier(self,objective):
	#This function queries our match database to determine what is the probability of winning the game if you get first drag, baron, tower, or blood.
	#objective is a string with values: 'Dragon', 'Baron', 'Blood', or 'Tower'
	#We separate the results by tier.  Tier is 'Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond', 'Master', 'Challenger'

		#List of stuff I need to accomplish:
			#1. Calculate the average tier of the players in a given match
			#2. From those matches, select those where objective = True.

		tier_dict = {'UNRANKED':None, 'BRONZE':1, 'SILVER':2,'GOLD':3,'PLATINUM':4,'DIAMOND':5,'MASTER':6,'CHALLENGER':7}
		#Later, we will store results of a computation in here.
		win_probability_dict = {}

		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data', charset='utf8')
		cur = conn.cursor()

		cur.execute('SELECT count(*) from matches')
		num_matches = cur.fetchone()[0]

		df = pd.DataFrame(index = (0,num_matches), columns=('matchId','matchTier',objective,'win') )
		df_row_counter = 0

		matches_sql = 'SELECT matchId, ' + objective + ', win FROM matches'
		cur.execute(matches_sql)
		matchIds = cur.fetchall()
		match_counter=0

		for m in matchIds:
			match_counter+=1
			#include some output to have an idea of progress
			if match_counter % 100 == 0:
				print(match_counter)

			matchId = m[0]
			cur.execute('SELECT tier FROM summonersjctmatches WHERE matchId = ' + str(matchId))
			tiers = cur.fetchall()
			
			#Initialize some variables used for computing the average tier of players in a match
			counter = 0
			match_tier=0

			#Find the sum of the tiers of the ranked players in the given match
			for t in tiers:
				t2 = t[0]
				if tier_dict[t2] != None:
					counter+=1
					match_tier += tier_dict[t2]
				else:
					continue

			#Here is the average tier of the ranked players in the match.  We are going to insist that we know the ranks of at least 3 players in the match. 
			if counter < 4:
				continue
			else:
				match_tier = match_tier/counter 
				who_got_objective = m[1]
				winner = m[2]

				df.loc[df_row_counter] = [matchId, match_tier, who_got_objective, winner]
				df_row_counter +=1

		#Now we run some statistical analysis on our dataframe.  
		for tier in tier_dict:
			#Skip unranked...
			if tier == 'UNRANKED':
				continue
			#Here we select the upper and lower bounds for the tier value of a match.  For example, a gold match has 2.5 < tier < 3.5.
			lower_bound = tier_dict[tier]-0.5
			upper_bound = tier_dict[tier]+0.5


			#Select those matches played at the specified tier and store the result in a dataframe
			matches_by_tier_df = df.loc[ (df['matchTier'] < upper_bound) & (df['matchTier'] > lower_bound) ]

			#Total number of matches played at a given tier		
			matches_by_tier_count = matches_by_tier_df.shape[0]
			print(matches_by_tier_count)
			#Total number of matches at a given tier where the objective was taken by team 200 AND team 200 won
			matches_by_tier_with_obj_and_win_count = matches_by_tier_df.loc[(matches_by_tier_df[objective]==True) & (matches_by_tier_df['win']==True)].shape[0]
			print(matches_by_tier_with_obj_and_win_count)

			#Probability of taking the objective and winning
			if matches_by_tier_count == 0:
				P_obj_and_win = 0
				continue
			else:
				P_obj_and_win = matches_by_tier_with_obj_and_win_count / matches_by_tier_count 

			#Probability of taking the objective is equal to half the probability that the objective gets taken at all
			P_obj = matches_by_tier_df.loc[ matches_by_tier_df[objective] != None ].shape[0] / matches_by_tier_count / 2
			#The overall conditional probability that you win, given that you took an objective is given by:
			P = P_obj_and_win/P_obj
			win_probability_dict.update({tier:P})

		#Return the probability of winning if team 200 is playing a match at a given tier and takes some objective first:
		#return 'If your team gets ' + objective + ' then your probability of winning is ' +str(P_obj_and_win/P_obj*100)+'%'
		plt.bar(range(len(win_probability_dict)), win_probability_dict.values(), align='center')
		plt.xticks(range(len(win_probability_dict)), win_probability_dict.keys())
		plt.show()

		return plt

	def find_best_bot_lane_duo(self,champId1,champId2):
		#INPUTS:
			#champId1 is a string containing the name of one of the 14 in-meta support champions
			#champId2 is a string containing the name of one of the 14 in-meta ADC champions

		#These dictionaries associate champ names to the categorical positions they indicate in the training data
		support_dict = {'Zyra':0, 'Bard':1,'Braum':2,'Soraka':3,'Leona':4, 'Janna':5,'Nami':6,'Karma':7,'Lulu':8,'Morgana':9,'Sona':10,'Blitzcrank':11,'Rakan':12,'Thresh':13}
		inverted_support_dict = {value:key for key,value in support_dict.items()}
		adc_dict = {'Caitlyn':0,'Xayah':1,'Lucian':2,'Draven':3,'Jinx':4, 'Vayne':5, 'Twitch':6, 'Ashe':7, 'Ezreal':8,'MissFortune':9,'Jhin':10,'Varus':11,'Tristana':12,'KogMaw':13}
		inverted_adc_dict = {value:key for key,value in adc_dict.items()}

		product_dict = {support_champ: {adc_champ: adc_dict[adc_champ]+ 14*support_dict[support_champ] for adc_champ in adc_dict} for support_champ in support_dict}
		inverted_product_dict = {i: (inverted_adc_dict[i%14],inverted_support_dict[int(i/14)]) for i in range(14*14)}

		
		conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data', charset='utf8')
		cur = conn.cursor()	
		cur.execute('SELECT * FROM meta_champs')

		#This dictionary associates champ names to their champIds
		meta_champs = {champ[0]:champ[1] for champ in cur.fetchall()}

		def get_data_from_db():

			#INPUTS: None
			#OUTPUTS: Numpy Arrays, bx and by, consisting of all training data.  The training data are as follows: 

			#The array bx[j] is a 28x1 np array.  The first 14 entries indicate the support player of the losing team; the next 14 entries indicate the ADC of the losing team.
			#The array by[j] is 196x1 np array, and represents the collection of ADC/support pairs (ordered lexicographically by support > adc)

			#Connect to the database
			conn = pymysql.connect(host='127.0.0.1', user='jmracek', passwd='', db='league_data', charset='utf8')
			cur = conn.cursor()	
			
			#SELECT all the matches in the DB such that all 4 champs in the bottom lane are in-meta.  Separate these by winners and losers.
			support_winners_query = "SELECT T.matchId, T.champId FROM (SELECT * FROM summonersjctmatches WHERE lane = \'BOTTOM\' AND matchId IN (SELECT matchId FROM summonersjctmatches WHERE lane=\'BOTTOM\' AND CASE WHEN role=\'DUO_SUPPORT\' THEN champId IN (SELECT champId FROM meta_supports) WHEN role=\'DUO_CARRY\' THEN champId IN (SELECT champId FROM meta_adc) END GROUP BY matchId HAVING count(*) = 4)) AS T LEFT JOIN matches ON T.matchId = matches.matchId WHERE T.team = CASE WHEN matches.win = 1 THEN 200 ELSE 100 END AND T.role=\'DUO_SUPPORT\' ORDER BY matchId;"
			support_losers_query = "SELECT T.matchId, T.champId FROM (SELECT * FROM summonersjctmatches WHERE lane = \'BOTTOM\' AND matchId IN (SELECT matchId FROM summonersjctmatches WHERE lane=\'BOTTOM\' AND CASE WHEN role=\'DUO_SUPPORT\' THEN champId IN (SELECT champId FROM meta_supports) WHEN role=\'DUO_CARRY\' THEN champId IN (SELECT champId FROM meta_adc) END GROUP BY matchId HAVING count(*) = 4)) AS T LEFT JOIN matches ON T.matchId = matches.matchId WHERE T.team = CASE WHEN matches.win = 1 THEN 100 ELSE 200 END AND T.role=\'DUO_SUPPORT\' ORDER BY matchId;"

			adc_winners_query = "SELECT T.matchId, T.champId FROM (SELECT * FROM summonersjctmatches WHERE lane = \'BOTTOM\' AND matchId IN (SELECT matchId FROM summonersjctmatches WHERE lane=\'BOTTOM\' AND CASE WHEN role=\'DUO_SUPPORT\' THEN champId IN (SELECT champId FROM meta_supports) WHEN role=\'DUO_CARRY\' THEN champId IN (SELECT champId FROM meta_adc) END GROUP BY matchId HAVING count(*) = 4)) AS T LEFT JOIN matches ON T.matchId = matches.matchId WHERE T.team = CASE WHEN matches.win = 1 THEN 200 ELSE 100 END AND T.role=\'DUO_CARRY\' ORDER BY matchId;"
			adc_losers_query = "SELECT T.matchId, T.champId FROM (SELECT * FROM summonersjctmatches WHERE lane = \'BOTTOM\' AND matchId IN (SELECT matchId FROM summonersjctmatches WHERE lane=\'BOTTOM\' AND CASE WHEN role=\'DUO_SUPPORT\' THEN champId IN (SELECT champId FROM meta_supports) WHEN role=\'DUO_CARRY\' THEN champId IN (SELECT champId FROM meta_adc) END GROUP BY matchId HAVING count(*) = 4)) AS T LEFT JOIN matches ON T.matchId = matches.matchId WHERE T.team = CASE WHEN matches.win = 1 THEN 100 ELSE 200 END AND T.role=\'DUO_CARRY\' ORDER BY matchId;"

			cur.execute(support_winners_query)
			winning_support_list = np.array(cur.fetchall())
			cur.execute(support_losers_query)
			losing_support_list = np.array(cur.fetchall())
			cur.execute(adc_winners_query)
			winning_carry_list = np.array(cur.fetchall())
			cur.execute(adc_losers_query)
			losing_carry_list = np.array(cur.fetchall())

			bx = np.zeros( (int(len(losing_support_list)), 28) )
			by = np.zeros( (int(len(winning_support_list)), 196) )
			index = 0
			
			for sup_win, sup_lose, adc_win, adc_lose in zip(winning_support_list, losing_support_list, winning_carry_list, losing_carry_list):

				#Record the losing support and adc
				bx[index][support_dict[meta_champs[sup_lose[1]]]] = 1
				bx[index][14+adc_dict[meta_champs[adc_lose[1]]]] = 1

				#Record the winning support and adc
				by[index][product_dict[meta_champs[sup_win[1]]][meta_champs[adc_win[1]]]] = 1

				#Count
				index += 1

			return bx, by



		#This function inputs your bot lane opponents, and outputs the best possible lane to play against them
		#The 'best' lane is determined by training a neural network against win/loss data.  We only consider the 14 in meta supports and 14 in meta ADCs.

		#The input to the neural network is an array of length #adc + #support champs.  The elements of the array represent categorical variables that describe the losing team's
		#champions.  An entry of the input array is 0 if the champ was not part of the losing team, and is equal to 1 if the champ was a part of the losing team.  The output of the
		#neural network is an array of length 14*14 

		#The neural network is trained on a sample of about 20000 games played

		#I am using code from: https://github.com/aymericdamien/TensorFlow-Examples/blob/master/examples/3_NeuralNetworks/multilayer_perceptron.py
		#in order to get myself in a position where I can get familiar with tensorflow.

		def train_lane_nn():

			# Parameters
			learning_rate = 0.001
			training_epochs = 15
			batch_size = 100
			display_step = 1

			# Network Parameters
			n_hidden_1 = 100 # 1st layer number of features
			n_hidden_2 = 100 # 2nd layer number of features
			n_input = 28 # 14 supports and 14 ADCs from the losing team
			n_classes = 196 # 14 supports x 14 adcs = 196 possible pairs of ADC/support combos

			# tf Graph input
			x = tf.placeholder("float", [None, n_input])
			y = tf.placeholder("float", [None, n_classes])

			# Create model with 2 hidden layers and an output layer
			def multilayer_perceptron(x, weights, biases):
	    		# Hidden layer with RELU activation
				layer_1 = tf.add(tf.matmul(x, weights['h1']), biases['b1'])
				layer_1 = tf.nn.relu(layer_1)
				# Hidden layer with RELU activation
				layer_2 = tf.add(tf.matmul(layer_1, weights['h2']), biases['b2'])
				layer_2 = tf.nn.relu(layer_2)
				# Output layer with linear activation.  This has been changed from the sourcecode to add a softmax to the output layer
				out_layer = tf.matmul(layer_2, weights['out']) + biases['out']
				return out_layer
	    	# Store layers weight & bias, randomly initialized.
			weights = {
			    'h1': tf.Variable(tf.random_normal([n_input, n_hidden_1])),
			    'h2': tf.Variable(tf.random_normal([n_hidden_1, n_hidden_2])),
			    'out': tf.Variable(tf.random_normal([n_hidden_2, n_classes]))
			}
			biases = {
			    'b1': tf.Variable(tf.random_normal([n_hidden_1])),
			    'b2': tf.Variable(tf.random_normal([n_hidden_2])),
			    'out': tf.Variable(tf.random_normal([n_classes]))
			}

			# Construct model
			pred = multilayer_perceptron(x, weights, biases)

			# Define loss and optimizer
			cost = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits=pred, labels=y))
			optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate).minimize(cost)

			# Initializing the variables
			init = tf.global_variables_initializer()

			# Launch the graph
			with tf.Session() as sess:
				sess.run(init)
				data, labels = get_data_from_db()

				training_data = data[:int(0.8*len(data))]
				training_labels = labels[:int(0.8*len(labels))]

				testing_data = data[int(0.8*len(data)):]
				testing_labels = labels[int(0.8*len(labels)):]

			    # Training cycle
				for epoch in range(training_epochs):
					avg_cost = 0.
					total_batch = int(len(training_data)/100) #Number of games, divided by number of matches per training batch

			        #Randomize the data
					np.random.shuffle(training_data)

			        # Loop over all batches
					for i in range(total_batch):
						batch_x, batch_y = training_data[i*100:(i+1)*100], training_labels[i*100:(i+1)*100]
						# Run optimization op (backprop) and cost op (to get loss value)
						_, c = sess.run([optimizer, cost], feed_dict={x: batch_x, y: batch_y})
						# Compute average loss
						avg_cost += c / total_batch
					# Display logs per epoch step
					if epoch % display_step == 0:
						print("Epoch:", '%04d' % (epoch+1), "cost=", \
							"{:.9f}".format(avg_cost))
				print("Optimization Finished!")

				# Test model
				correct_prediction = tf.equal(tf.argmax(pred, 1), tf.argmax(y, 1))
				# Calculate accuracy
				#accuracy = tf.reduce_mean(tf.cast(correct_prediction, "float"))
				#print("Accuracy:", accuracy.eval({x: testing_data, y: testing_labels}))

				prediction = tf.argmax(pred,1)
				out = prediction.eval({x: np.array([1 if i in set([support_dict[champId1], adc_dict[champId2]+14]) else 0 for i in range(28)]).reshape( (1,28) ) })
				print('The best lane against ' + champId1 + ' and ' + champId2 + ' is:' )
				print(inverted_product_dict[out[0]])
				#print('The best lane against ' + champId1 + ' and ' + champId2 + ' is '#)

		train_lane_nn()