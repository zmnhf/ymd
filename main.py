import os
import re
import configparser
from time import time
import subprocess
import json

import pandas as pd
from joblib import Parallel, delayed
from youtube_dl import YoutubeDL
from mutagen.mp4 import MP4
import emoji

OUTPUT_DIR = GENRE = FORMAT = FILENAME = JOBS = RM_EMOJI = SV_THUMB = ""
URLLIST = []
CNT = 0
ERROR_LIST = []

def get_global():
	"""変数の設定"""
	global OUTPUT_DIR, GENRE, FORMAT, FILENAME, JOBS, RM_EMOJI, SV_THUMB, URLLIST

	try:
		ini = configparser.ConfigParser()
		ini.read("settings.ini", encoding="utf-8_sig")
		OUTPUT_DIR = ini["env"]["output_dir"]
		GENRE = ini["env"]["genre"]
		FORMAT = ini["env"]["format"]
		FILENAME = ini["env"]["filename"]
		JOBS = ini["env"]["n_jobs"]
		RM_EMOJI = eval(ini["env"]["remove_emoji"])
		SV_THUMB = eval(ini["env"]["save_thumbnail"])
	except Exception as e:
		print(f"{e}\nsettings.ini読み込みエラーだよ～項目すべて書いてある？")

	if OUTPUT_DIR == "":
		OUTPUT_DIR = os.getcwd()
	if (JOBS == "") or (type(JOBS) is not int):
		JOBS = -1

	try:
		with open("urllist.txt", encoding="utf-8_sig") as f:
			for i in f:
				i = i.strip(os.linesep)
				a = i.split(" ")
				URLLIST.append(a)
	except Exception as e:
		print(f"{e}\nurllist.txt読み込みエラーだよ～")

def fix_title(s):
	"""ファイル名に使ってはいけない文字の削除"""
	s = re.sub(r"[\\/:.,;*?\"\'<>|\-]", "", s)
	s = s.replace(" ", "_")
	return s

def remove_emoji(s):
	"""androidにコピーする時ファイル名に絵文字あると失敗する。MTPのバグ？"""
	return "".join(i for i in s if i not in emoji.UNICODE_EMOJI)

def stopwatch(func):
	def wrapper(*args, **kwargs):
		start = time()
		func(*args, **kwargs)
		stop = time()
		print(f"{stop-start}秒かかりました")
	return wrapper

class SetChannel:
	def __init__(self, artist, playlists):
		self.artist = artist
		self.playlists = []
		self.playlists.extend(playlists)
		self.path = os.path.join(OUTPUT_DIR, self.artist)
		self.path_csv = os.path.join(self.path, "list.csv")
		self.csv_df = self.mk_csv()

	def mk_csv(self):
		"""csv_df更新"""
		print(f"{self.artist}のmk_csv開始")
		os.makedirs(self.path, exist_ok=True)
		if os.path.exists(self.path_csv):
			oldcsv_df = pd.read_csv(self.path_csv, index_col=0)
		else:
			oldcsv_df = pd.DataFrame(columns=["flag", "date", "id", "title"]) #flagはDL判定(TrueでDL済み)

		newcsv_df = pd.DataFrame(columns=["flag", "date", "id", "title"])
		for i in self.playlists:
			newcsv_df = pd.merge(newcsv_df, self.mk_playlist_df_subp(i), how="outer") #######################
		newcsv_df = newcsv_df.drop_duplicates(["id"]).sort_values(by=["date"]).reset_index(drop=True)

		newcsv_df = pd.merge(newcsv_df.drop(columns=["flag"]), oldcsv_df.drop(columns=["title", "date"]), on="id", how="left")
		newcsv_df = newcsv_df[["flag", "date", "id", "title"]] #列並び替え
		print(newcsv_df)
		return newcsv_df

	def mk_playlist_df(self, playlisturl): #v2020.01.24 ignoreerrors:Trueが効かない問題あり
		"""playlistのurlからnewcsv_dfをreturn"""
		opts = {
			"ignoreerrors": True,
			"quiet": True,
			"simulate": False}
		with YoutubeDL(opts) as ydl:
			try:
				playlist_dict = ydl.extract_info(playlisturl, download=False)
				flag_l = [False for i in playlist_dict["entries"]]
				date_l = [i.get("upload_date") for i in playlist_dict["entries"]]
				id_l = [i.get("id") for i in playlist_dict["entries"]]
				if RM_EMOJI == True:
					title_l = [remove_emoji(fix_title(i.get("title"))) for i in playlist_dict["entries"]]
				else:
					title_l = [fix_title(i.get("title")) for i in playlist_dict["entries"]]
				newcsv_df = pd.DataFrame(data={"flag": flag_l, "date":date_l, "id":id_l, "title":title_l})
				return newcsv_df

			except Exception as e:
				print(f"{e}\n{self.artist}のプレイリストurlが正しくないっぽいよ～404じゃないかな～")
				return pd.DataFrame(columns=["flag", "date", "id", "title"])

	def mk_playlist_df_subp(self, playlisturl): #ignoreerrors:Trueが効かないやつの対応
		json_path = os.path.join(self.path, "playlist.json")
		try:
			os.remove(json_path)
		except:
			pass

		command = ["youtube-dl", playlisturl, "-i", "-j", ">>", json_path]
		a = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
#		if "ERROR:" in str(a.stdout):
#			print(f"{str(a.stdout)}\n{self.artist}のプレイリスト{playlisturl}でエラーだよ～404じゃないかな～")
#			return pd.DataFrame(columns=["flag", "date", "id", "title"])

		with open(json_path, "r") as f:
			j = [json.loads(i) for i in f]
			flag_l = [False for i in j]
			date_l = [i["upload_date"] for i in j]
			id_l = [i["id"] for i in j]
			if RM_EMOJI == True:
				title_l = [remove_emoji(fix_title(i["title"])) for i in j]
			else:
				title_l = [fix_title(i["title"]) for i in j]
			newcsv_df = pd.DataFrame(data={"flag": flag_l, "date":date_l, "id":id_l, "title":title_l})

		os.remove(json_path)
		return newcsv_df

class SetVideo:
	def __init__(self, Channel, trkn, row):
		self.artist = Channel.artist
		self.path = Channel.path
		self.date = row["date"]
		self.id = row["id"]
		self.title = row["title"]
		self.trkn = trkn
		self.filename = self.set_filename()
		self.path_m4a = os.path.join(self.path, self.filename)

	def set_filename(self):
		"""settings.iniで指定したフォーマットでファイル設定"""
		#trkn_form = format(self.trkn, "02")
		s = FILENAME
		s = s.replace("_artist_", "str(self.artist)")
		s = s.replace("_date_", "str(self.date)")
		s = s.replace("_id_", "str(self.id)")
		s = s.replace("_title_", "str(self.title)")
		s = s.replace("_trkn_", "str(self.trkn)")
		return eval(s)

	def start_dl(self):
#		print(f"{self.filename}のDL開始します")
		self.ydl_m4a()
		self.tagging()

	def ydl_m4a(self):
#		print(f"{self.filename}のDL開始します")
		opts = {
			"ignoreerrors": True,
			"quiet": False,
			"simulate": False,
			"outtmpl": self.path_m4a,
			"writethumbnail": True,
			"format": FORMAT}
		with YoutubeDL(opts) as ydl:
			ydl.download([self.id]) #引数はリストで与えないと検索してDLしちゃうっぽい

	def tagging(self):
		"""mutagenでタグ付け"""
#		print(f"{self.filename}のタグ付け開始します")
		audio = MP4(self.path_m4a)
		audio["\xa9nam"] = [self.title] #タイトル
		audio["\xa9ART"] = [self.artist] #アーティスト
		audio["\xa9alb"] = [self.artist] #アルバム
		audio["\xa9gen"] = [GENRE] #ジャンル
		audio["trkn"] = [(self.trkn, 0)] #トラックナンバー
		#youtube_dlでは何故かアルバムアート設定できなかったのでmutagenで設定
		with open(self.path_m4a.replace(".m4a", ".jpg"), "rb") as c:
			c = c.read()
			audio["covr"] = [c]
		audio.save()

	def rm_thumb(self):
		os.remove(str(self.path_m4a).replace(".m4a", ".jpg"))

@stopwatch
def main():
	global CNT, ERROR_LIST
	get_global()
	Parallel(n_jobs=JOBS, require="sharedmem")(delayed(instantize)(i) for i in URLLIST)

	print("-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=")
	print(f"計{CNT}個のDLしました")
	if not ERROR_LIST == []:
		print(ERROR_LIST)

def instantize(i):
	"""インスタンス化してDL開始、実質のmain()"""
	global CNT, ERROR_LIST
	artist, playlists = i[0], i[1:]
	if len(playlists) == 0:
		return
	Channel = SetChannel(artist, playlists)
	for i, row in Channel.csv_df.iterrows():
		if row["flag"] == True:
			pass
		else:
			trkn = i + 1
			Video = SetVideo(Channel, trkn, row)
			try:
				Video.start_dl()
				Channel.csv_df.loc[[i], ["flag"]] = True
				CNT += 1
				if SV_THUMB == False:
					Video.rm_thumb()
			except Exception as e:
				Channel.csv_df.loc[[i], ["flag"]] = False
				error_msg = f"{Video.artist, Video.id, Video.filename}のDLに失敗しています"
				ERROR_LIST.append(error_msg)
	Channel.csv_df.to_csv(Channel.path_csv)

@stopwatch
def test():
	pass

if __name__ == "__main__":
	main()
#	test()
