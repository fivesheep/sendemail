#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2008 Young Ng <fivesheep@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,  but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#

import os
import sys
import md5
import time
import uuid
import traceback
import socket
import smtplib

from ConfigParser import ConfigParser
from getpass import getpass
from optparse import OptionParser
from StringIO import StringIO
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formatdate, make_msgid
from email import encoders
from smtplib import SMTP
from smtplib import SMTPServerDisconnected
from sys import stderr,stdout


class ConsoleUI:
    def __init__(self):
        self._isInitialized=0
        self._setupTerm()
    
    def _setupTerm(self):
        import curses
        curses.setupterm()
        self.COLS=curses.tigetnum('cols')
        self.BOL=curses.tigetstr('cr')
        self.UP=curses.tigetstr('cuu1')
        self.CLEAR_EOL=curses.tigetstr('el')
        
    def initProgressBar(self,total=100,finished=0,status=""):
        self._isInitialized=1
        self.total=total
        self.finished=finished
        self.status=status
        stdout.write('\n')
    
    def updateProgressBar(self,total,finished,status=None):
        if self._isInitialized:
            self.total=total
            self.finished=finished
            if status is not None:
                self.status=status
            self._renderBar()
        else:
            self.initProgressBar()
    
    def stopProgressBar(self):
        self._isInitialized=0
        stdout.write('\n')
        stdout.flush()
        
    def message(self,msg):
        stdout.write(msg)
        stdout.write('\n')
        stdout.flush()
        
    def updateStatus(self,status):
        if self._isInitialized:
            self.status=status
            self._renderBar()
        else:
            self.message(status)
    
    def _renderBar(self):
        if self._isInitialized:
            ratio=float(self.finished)/self.total
            f=int((self.COLS-20)*ratio)
            nof=self.COLS-20-f
            stdout.write(self.BOL+self.UP+self.CLEAR_EOL+" [%s%s] %.1f%%\n"%('x'*f,'-'*nof,100*ratio))
            if self.status is not None:
                stdout.write(self.BOL+self.CLEAR_EOL+" Status: "+self.status)
            stdout.flush()

class ObservableSMTP(SMTP):
    def __init__(self,host='',port=0,local_hostname=None):
        SMTP.__init__(self, host, port, local_hostname)
        # Use a dummy function for the progress bar
        self.progressBar=lambda total,sent,speed,elapsed:None
        self.ui=None
        
    def setUI(self,ui):
        self.ui=ui
    
    def _updateProgress(self,total,sent,status):
        if self.ui is not None:
            self.ui.updateProgressBar(total,sent,status)
    
    def send(self, str):
        """Send `str' to the server."""
        if self.debuglevel > 0: print>>stderr, 'send:', repr(str)
        if self.sock:
            total=len(str)    # the total length of the str(the email) to be sent
            sent=0            # the size of the sent data (c)
            buff=2048         # buff size (c)
            try:
                while(1):
                    self._updateProgress(total,sent,"Transferring...... (%d/%d)"%(sent,total))
                    #self.ui.updateStatus("Transferring...... (%d/%d)"%(sent,total))
                    begin,end=sent,sent+buff
                    data=str[begin:end]
                    len_of_data=len(data)
                    if len_of_data>0:
                        self.sock.sendall(data)
                        sent+=len_of_data
                    else:
                        break

            except socket.error:
                self.close()
                raise SMTPServerDisconnected('Server not connected')
        else:
            raise SMTPServerDisconnected('please run connect() first')
        
    def putcmd(self,cmd,args=""):
        """Send a command to the server."""
        if args=="":
            str='%s%s'%(cmd,smtplib.CRLF)
        else:
            str='%s %s%s'%(cmd,args,smtplib.CRLF)
        # Don't monitor the command data
        SMTP.send(self,str)

class GSSender(object):
    SIZE_OF_MEGA_BYTE=1024*1024

    def __init__(self,host,port,login, paswd,attachment_size=5,
                    email_encoding='utf-8',fs_encoding='utf-8', tls=True,ui=None):
        self.host=host
        self.port=port
        self.login=login
        self.fromAddr=login
        self.paswd=paswd
        self.smtp=None
        self.smtp_connected=False
        self.tls=tls
        self.encoding=email_encoding
        self.fs_encoding=fs_encoding
        self.maxAttSize=attachment_size*GSSender.SIZE_OF_MEGA_BYTE
        self.encoding=email_encoding
        if fs_encoding==None:
            self.fs_encoding=sys.getfilesystemencoding()
        else:
            self.fs_encoding=fs_encoding
        self.ui=ui
        
    def send(self,toAddrs,attachment,additional_text=''):
        """Send the file to the addrs"""
        # get the file infos
        filename=os.path.basename(attachment)
        attachfile=open(attachment.encode(self.fs_encoding), 'rb')
        self.ui.message("Preparing...")
        try:
            # caculate the md5sum for the file
            md5sum=self._md5sum(attachfile)
            # after caculating the md5sum, the cursor is now at the end of the file object
            # the offset of the cursor is indeed the file size.
            size=attachfile.tell()
            # go back to the beginning of the file for further use
            attachfile.seek(0)
            
            fid=uuid.uuid1().__str__()
            
            fromAddr=self.fromAddr
       
            if size<self.maxAttSize:
                # send one email
                self.ui.message("There is 1 email to send.")
                fobj=attachfile.read()
                subject="[GS_SINGLE][Name: %s]"%filename
                text=self._composeFileInfo(filename,size,fid,md5sum,additional_text)
                msg=self._buildBaseMsg(fromAddr, toAddrs, subject, text)
                msg.attach(self._buildAttachmentPart(filename,fobj))
                
                self.ui.message("Sending email 1 of 1.")
                self._doSend(toAddrs,msg)
                self.ui.message("Job finished!")
            else:
                # send multi emails with a summary
                num_of_packages=int(size/self.maxAttSize)+(size%self.maxAttSize and 1 or 0)
                self.ui.message("There are %d packages and 1 summary to send."%num_of_packages)
                package_count=0
                # Send the packages of the file
                while(1):
                    fobj=attachfile.read(self.maxAttSize)
                    package_size=len(fobj)
                    if package_size == 0:
                        break
                    package_checksum=self._buffMd5sum(fobj)
    
                    subject="[GS_PART][NAME: %s][%03d]"%(filename, package_count)
                    text=self._composeFileInfo(filename,size,fid,md5sum,additional_text,
                             0,package_count,package_size,package_checksum)
                    msg=self._buildBaseMsg(fromAddr, toAddrs, subject, text)
                    msg.attach(self._buildAttachmentPart("%s.%03d"%(filename, package_count),fobj))
    
                    self.ui.message("Sending package %d of %d."%(package_count+1,num_of_packages))
                    self._doSend(toAddrs,msg)
                    package_count+=1
                
                # Send the summary of the file
                subject="[GS_SUM][NAME: %s]"%(filename)
                text=self._composeFileInfo(filename,size,fid,md5sum,additional_text,
                             num_of_packages=package_count)
                msg=self._buildBaseMsg(fromAddr, toAddrs, subject, text)
                self.ui.message("Sending the summary.")
                self._doSend(toAddrs,msg)
                self.ui.message("Job finished.")
        finally:
            attachfile.close()
            self._disconnect()
    
    def _doSend(self, toAddrs, msg, retry_times=5):
        retry_count=0
        self.ui.initProgressBar()
        while(1):
            try:
                if not self.smtp_connected:
                     self._connect()
                self.smtp.sendmail(self.fromAddr,toAddrs, msg.as_string())
                break
            except smtplib.SMTPException:
                retry_count+=1
                if retry_count<retry_times:
                    self.ui.updateStatus("Transferring failed, the email will be re-sent in 5 secs.")
                    time.sleep(5)
                else:
                    self.ui.updateStatus("Max retry times reached, sending failed!")
                    break
                #traceback.print_exc()
        self.ui.stopProgressBar()

    def _isConnected(self):
        try:
            self.smtp.noop()
            return True
        except:
            return False

    def _connect(self):
        if self._isConnected():
            return
        try:
            self.ui.updateStatus("Connecting to the SMTP server.")
            smtp = ObservableSMTP(self.host,self.port)
            smtp.setUI(self.ui)
            if self.tls:
                self.ui.updateStatus('Tring TLS authentication.')
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            self.ui.updateStatus('Logging in....')
            smtp.login(self.login,self.paswd)
            self.ui.updateStatus('Login Ok!')
            self.smtp=smtp
            self.smtp_connected=True
        except smtplib.SMTPAuthenticationError :
            self.ui.updateStatus("Authentication failed!")
            sys.exit(2)
        except smtplib.SMTPException,e:
            #traceback.print_exception()
            self.ui.updateStatus("SMTP Exception!")

    def _disconnect(self):
        try:
            self._isConnected()
            self.smtp.quit()
        finally:
            #print "SMTP server disconnected!"
            self.smtp_connected=False
            pass

    def _md5sum(self,fileobj):
        """Caculate the md5check of a fileobject"""
        m=md5.new()
        # For better performance, don't read the whole file at a time.
        while(1):
            buff=fileobj.read(GSSender.SIZE_OF_MEGA_BYTE)
            if len(buff)==0:
                break
            m.update(buff)
      
        return m.hexdigest()
    
    def _buffMd5sum(self,buff):
        """Caculate the md5check of a string"""
        m=md5.new()
        m.update(buff)
        return m.hexdigest()
    
    def _buildAttachmentPart(self,attName,buff):
        """build an attachment part for the email msg"""
        part = MIMEBase('application', "octet-stream")
        part.set_payload( buff )
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 
                        ('attachment; filename="%s"'%attName).encode(self.encoding))
        return part
    
    
    def _buildBaseMsg(self, fromAddr, toAddrs, subject, text, cc=[], bcc=[]):
        """Build msg with no attachments"""
        encoding=self.encoding
        msg=MIMEMultipart(_charset=encoding)
        msg['From']=fromAddr
        if len(toAddrs)>0:
            msg['To']=COMMASPACE.join(toAddrs)
        if len(cc)>0:
            msg['cc']=COMMASPACE.join(cc)
        if len(bcc)>0:
            msg['Bcc']=COMMASPACE.join(bcc)
        msg['Subject'] = subject.encode(encoding)
        msg['Date'] = formatdate(localtime=True)
        msg['Message-ID']=make_msgid()
        body=MIMEText(text.encode(encoding),_subtype='plain',_charset=encoding)
        msg.attach( body )

        return msg

    def _composeFileInfo(self,filename,size,fid,md5sum,additional_text='',num_of_packages=0,
                         package_id=-1,package_size=0,package_checksum=None):
            text=StringIO()
            text.write('File Name: %s\n'%filename)
            text.write('Total Size: %d\n'%size)
            text.write('FID: %s\n'%fid)
            if num_of_packages!=0:
                text.write('Num of Packages: %d\n'%(num_of_packages))
            text.write('Md5sum: %s\n'%md5sum)
 
            if package_id!=-1:
                text.write('-'*100+'\n')
                text.write('Package Id: %03d\n'%(package_id))
            if package_size!=0:
                text.write('Package Size: %d\n'%package_size)
            if package_checksum!=None:
                text.write('Package Md5sum: %s\n'%package_checksum)
            if len(additional_text)>0:
                text.write('-'*100+'\n')
                text.write(additional_text)
            txt=text.getvalue()
            text.close()
            return txt
    


class Main(object):
    def __init__(self):
        self.loadConfig()
        self.parseOpts()
        self.ui=ConsoleUI()
        self.sender=GSSender(self.host,
                             self.port,
                             self.login,
                             self.paswd,
                             self.attachment_size,
                             self.encoding,
                             self.fsencoding,
                             self.tls,
                             self.ui)
    
    def parseOpts(self):
        usage="usage: %prog [options] addr1 addr2 ..."
        parser=OptionParser(usage=usage)
        parser.add_option('-f','--file',dest='filename', 
                          help="the file to send")
        parser.add_option('-p','--password',action='store_true',dest='paswd', 
                          help="the passwd for logging into the smtp server")
        (opts,args)=parser.parse_args()
        self.opts=opts
        self.args=args

        filename=self.opts.filename
        if filename==None:
            print >>sys.stderr,"You must specify the file to send!"
            parser.print_usage()
            sys.exit(2)
        else:
            if not os.path.exists(filename):
                print >>sys.stderr, "File '%s' not exist!"%filename
                sys.exit(2)
            elif not os.path.isfile(filename):
                print >>sys.stderr, "'%s' is not a file."%filename
                sys.exit(2)
            elif not os.access(filename, os.R_OK):
                print >>sys.stderr, "You have no permission to read the file '%s'."%filename
                sys.exit(2)

        if self.opts.paswd==True or self.paswd == None or self.paswd =='':
            self.paswd=getpass("Please enter the password:")

        if not self.args:
            print >>sys.stderr, "There's no receiver provided!"
            parser.print_usage()
            sys.exit(2)
        
    def loadConfig(self):
        confpath=os.path.expanduser('~/.gsend/gsend.conf')
        conf=ConfigParser()
        if not os.path.exists(confpath):
            os.makedirs(os.path.expanduser('~/.gsend'))
        if not os.path.exists(confpath):    
            print >>sys.stderr,"Before using this program, you have to complete the config file."
            print >>sys.stderr,"The the path of the config file is '~/.gsend/gsend.conf'"
            conf.add_section("SERVER")
            conf.add_section("ACCOUNT")
            conf.add_section("OPTIONS")
            conf.set('SERVER','host','smtp.gmail.com')
            conf.set('SERVER','port',587)
            conf.set('SERVER','tls',True)
            conf.set('ACCOUNT','login','example@gmail.com')
            conf.set('ACCOUNT','paswd','')
            conf.set('OPTIONS','email_encoding','utf-8')
            conf.set('OPTIONS','file_system_encoding',sys.getfilesystemencoding())
            conf.set('OPTIONS','attachment_size',5)
            conffile=open(confpath,'w')
            conf.write(conffile)
            conffile.close()
            sys.exit(2)
        else:
            conf.read(confpath)
            
            self.host=conf.get('SERVER','host')
            self.port=conf.getint('SERVER','port')
            self.tls=conf.getboolean('SERVER','tls')
            self.login=conf.get('ACCOUNT','login')
            self.paswd=conf.get('ACCOUNT','paswd')
            
            # password is not provided in the conf file,
            # ask the user to input 
            
            self.attachment_size=conf.getint('OPTIONS','attachment_size')
            self.encoding=conf.get('OPTIONS','email_encoding')
            self.fsencoding=conf.get('OPTIONS','file_system_encoding')        
    
    def run(self):
        try:
            self.sender.send(self.args, self.opts.filename)
        except (KeyboardInterrupt,SystemExit):
            print "\nExiting the program...."
            exit(2)
        except :
            traceback.print_exc()
            print >>sys.stderr,"Unhandled exception occurred, program terminated unexceptedly!"
            exit(2)
                

if __name__=='__main__':
    main=Main()
    main.run()
    sys.exit(0)

