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
from sys import stderr


class ObservableSMTP(SMTP):
    def __init__(self,host='',port=0,local_hostname=None):
        SMTP.__init__(self, host, port, local_hostname)
        # Use a dummy function for the progress bar
        self.progressBar=lambda total,sent,speed,elapsed:None
        
    def setProgressBar(self,progressBar):
        self.progressBar=progressBar
    
    def _updateProgress(self,total,sent,speed,elapsed):
        self.progressBar(total,sent,speed,elapsed)
    
    def send(self, str):
        """Send `str' to the server."""
        if self.debuglevel > 0: print>>stderr, 'send:', repr(str)
        if self.sock:
            total=len(str)    # the total length of the str(the email) to be sent
            sent=0            # the size of the sent data (c)
            buff=2048         # buff size (c)
            speed=0           # the sending speed, (cps)
            elapsed=0.0001    # the elapsed time, (s)
            try:
                start_time=time.clock()
                while(1):
                    self._updateProgress(total,sent,speed,elapsed)
                    begin,end=sent,sent+buff
                    data=str[begin:end]
                    len_of_data=len(data)
                    if len_of_data>0:
                        self.sock.sendall(data)
                        elapsed=time.clock()-start_time
                        sent+=len_of_data
                        speed=sent/elapsed
                    else:
                        break

            except socket.error:
                self.close()
                raise SMTPServerDisconnected('Server not connected')
        else:
            raise SMTPServerDisconnected('please run connect() first')


class GSMsgBuilder(object):
    """
        This class is used to build emails.
    """
    SIZE_OF_MEGA_BYTE=1024*1024

    def __init__(self, maxAttSizeMb=5,email_encoding='utf-8',fs_encoding=None):
        self.maxAttSize=maxAttSizeMb*GSMsgBuilder.SIZE_OF_MEGA_BYTE
        self.encoding=email_encoding
        if fs_encoding==None:
            self.fs_encoding=sys.getfilesystemencoding()
        else:
            self.fs_encoding=fs_encoding
        
    def _md5sum(self,fileobj):
        """Caculate the md5check of a fileobject"""
        m=md5.new()
        # For better performance, don't read the whole file at a time.
        while(1):
            buff=fileobj.read(GSMsgBuilder.SIZE_OF_MEGA_BYTE)
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
    
    
    def buildBaseMsg(self, fromAddr, toAddrs, subject, text, cc=[], bcc=[]):
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

    def buildMsg(self, fromAddr, toAddrs, subject, text, cc=[], bcc=[], attachments=[]):
        """Build an email msg object with all the regular options"""
        # Build the base msg
        msg=self.buildBaseMsg(self, fromAddr, toAddrs, subject, text, cc, bcc,attachments)

        # Attach the files
        for att in attachments:
            msg.attach(self._buildAttachmentPart(os.path.basename(att),
                                             open(att.encode(self.fs_encoding),"rb").read()))
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

    def buildFileMsgs(self, fromAddr, toAddrs, attachment, additional_text=''):
        """Build one or more msgs(emails) for the file being sent"""
        # bcc=toAddrs     # Use bcc by default
        toAddrs=[]  # Hide the addrs
        
        # get the file infos
        filename=os.path.basename(attachment)
        attachfile=open(attachment.encode(self.fs_encoding), 'rb')
        # caculate the md5sum for the file
        md5sum=self._md5sum(attachfile)
        # after caculating the md5sum, the cursor is now at the end of the file object
        # the offset of the cursor is indeed the file size.
        size=attachfile.tell()
        # go back to the beginning of the file for further usage
        attachfile.seek(0)
        
        fid=uuid.uuid1().__str__()
        msgs=[]
   
        if size<self.maxAttSize:
            # build one email
            fobj=attachfile.read()
            subject="[GS_SINGLE][Name: %s]"%filename
            text=self._composeFileInfo(filename,size,fid,md5sum,additional_text)
            msg=self.buildBaseMsg(fromAddr, toAddrs, subject, text)
            msg.attach(self._buildAttachmentPart(filename,fobj))
            
            msgs.append(msg)
        else:
            # build multi emails with a summary
            package_count=0
            while(1):
                fobj=attachfile.read(self.maxAttSize)
                package_size=len(fobj)
                if package_size == 0:
                    break
                package_checksum=self._buffMd5sum(fobj)

                subject="[GS_PART][NAME: %s][%03d]"%(filename, package_count)
                text=self._composeFileInfo(filename,size,fid,md5sum,additional_text,
                         0,package_count,package_size,package_checksum)
                msg=self.buildBaseMsg(fromAddr, toAddrs, subject, text)
                msg.attach(self._buildAttachmentPart("%s.part.%03d"%(filename, package_count),fobj))

                msgs.append(msg)
                package_count+=1

            subject="[GS_SUM][NAME: %s]"%(filename)
            text=self._composeFileInfo(filename,size,fid,md5sum,additional_text,
                         num_of_packages=package_count)
            msg=self.buildBaseMsg(fromAddr, toAddrs, subject, text)

            msgs.append(msg)

        attachfile.close()

        return msgs

class GSSender(object):
    def __init__(self,host,port,login, paswd,attachment_size=5,email_encoding='utf-8',fs_encoding=None, tls=True):
        self.host=host
        self.port=port
        self.login=login
        self.fromAddr=login
        self.paswd=paswd
        self.smtp=None
        self.smtp_connected=False
        self.tls=tls
        self.builder=GSMsgBuilder(attachment_size,email_encoding,fs_encoding)

    def sendFiles(self,toAddrs, files):
        builder=self.builder

        for f in files:
            # 1. Build the msgs for the file
            msgs=builder.buildFileMsgs(self.fromAddr, toAddrs, f)
            print "Sending file: %s"%os.path.basename(f)
            # 2. Send the msgs respectively
            for msg in msgs:
                while(1):
                    if self._doSend(toAddrs, msg)==True:
                        # TODO: Sleep+Retry times
                        break
            print "File '%s' was sent successfully."%os.path.basename(f)

    def _connect(self):
        if self.smtp_connected==True:
            try:
                self.smtp.noop()
                # Already connected return directly.
                return
            except:
                self.smtp_connected=False

        try:
            print "Connecting to SMTP server."
            smtp = SMTP(self.host,self.port)
            if self.tls:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            print "Logging in..."
            smtp.login(self.login,self.paswd)
            print "Login OK!"
            self.smtp=smtp
            self.smtp_connected=True
        except :
            traceback.print_exc()
            print >>sys.stderr,"Unable to connect!!!"

    def _disconnect(self):
        try:
            self.smtp_connected=False
            self.smtp.quit()
        finally:
            print "SMTP server disconnected!"

    def _doSend(self, toAddrs, msg):
        try:
            self._connect()
            if self.smtp_connected:
                self.smtp.sendmail(self.fromAddr,toAddrs, msg.as_string())
                return True
        except :
            print >>sys.stderr,"Failed on sending msg!"
            traceback.print_exc()

        return False


class Main(object):
    def __init__(self):
        self.loadConfig()
        self.parseOpts()
        self.sender=GSSender(self.host,
                             self.port,
                             self.login,
                             self.paswd,
                             self.attachment_size,
                             self.encoding,
                             self.fsencoding,
                             self.tls)
    
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
                print >>sys.stderr, "You don't have the permission to read the file '%s'."%filename
                sys.exit(2)
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
            if self.paswd==None or self.paswd=='':
                self.paswd=getpass("Please enter the password:")
            
            self.attachment_size=conf.getint('OPTIONS','attachment_size')
            self.encoding=conf.get('OPTIONS','email_encoding')
            self.fsencoding=conf.get('OPTIONS','file_system_encoding')        
    
    def run(self):
        try:
            self.sender.sendFiles(self.args, [self.opts.filename])
        except:
            print sys.stderr,"Unhandle exception"
            exit(2)
                

if __name__=='__main__':
    main=Main()
    main.run()
