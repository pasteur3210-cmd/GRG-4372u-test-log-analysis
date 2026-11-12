"""GRG-4372u MP Analyzer V1.1.1; Windows GUI or CLI."""
import argparse,json,sys,traceback
from pathlib import Path
from analyzer import analyze,VERSION

def gui():
 import tkinter as tk
 from tkinter import ttk,filedialog,messagebox
 root=tk.Tk();root.title('GRG-4372u MP Log Analyzer V'+VERSION);root.geometry('850x230')
 src=tk.StringVar();dst=tk.StringVar();msg=tk.StringVar(value='匯入新批次 MP ZIP，自動分析七站與 MP RF LOG 規格。')
 def choose():
  p=filedialog.askopenfilename(filetypes=[('ZIP files','*.zip')]);
  if p:src.set(p);dst.set(str(Path(p).with_suffix(''))+'_MP_Analysis.xlsx')
 def output():
  p=filedialog.asksaveasfilename(defaultextension='.xlsx',filetypes=[('Excel','*.xlsx')]);
  if p:dst.set(p)
 ttk.Label(root,text='MP Log ZIP').grid(row=0,column=0,padx=8,pady=12);ttk.Entry(root,textvariable=src,width=85).grid(row=0,column=1);ttk.Button(root,text='選擇',command=choose).grid(row=0,column=2,padx=7)
 ttk.Label(root,text='報告 Excel').grid(row=1,column=0,padx=8,pady=12);ttk.Entry(root,textvariable=dst,width=85).grid(row=1,column=1);ttk.Button(root,text='選擇',command=output).grid(row=1,column=2,padx=7)
 def run():
  if not src.get():messagebox.showwarning('請先選擇ZIP','請選擇 MP Log ZIP');return
  if not dst.get():dst.set(str(Path(src.get()).with_suffix(''))+'_MP_Analysis.xlsx')
  try:
   msg.set('執行中……');root.update_idletasks();res=analyze(src.get(),dst.get());msg.set(f'完成：{res["event_count"]} 筆測試事件、{res["csv_count"]} 個RF CSV、{res["sheet_count"]} 個工作表');messagebox.showinfo('產出完成',msg.get()+'\n'+dst.get())
  except Exception as e:
   msg.set('失敗：'+str(e));messagebox.showerror('程式執行失敗',str(e))
 ttk.Button(root,text='開始分析並產出報告',command=run).grid(row=2,column=1,pady=12)
 ttk.Label(root,textvariable=msg).grid(row=3,column=0,columnspan=3,padx=10,sticky='w');root.mainloop()

def main():
 p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output');p.add_argument('--version',action='store_true');a=p.parse_args()
 if a.version:print(VERSION);return
 if a.input:
  output=a.output or str(Path(a.input).with_suffix(''))+'_MP_Analysis.xlsx';print(json.dumps(analyze(a.input,output),ensure_ascii=False,indent=2))
 else:gui()
if __name__=='__main__':main()
