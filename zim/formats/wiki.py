# -*- coding: utf-8 -*-

# Copyright 2008, 2012 Jaap Karssenberg <jaap.karssenberg@gmail.com>

'''This module handles parsing and dumping wiki text'''

import re

from zim.parser import *
from zim.formats import *
from zim.parsing import Re, TextBuffer, url_re


WIKI_FORMAT_VERSION = 'zim 0.4'


info = {
	'name': 'wiki',
	'desc': 'Zim Wiki Format',
	'mimetype': 'text/x-zim-wiki',
	'extension': 'txt',
	'native': True,
	'import': True,
	'export': True,
}


bullets = {
	'[ ]': UNCHECKED_BOX,
	'[x]': XCHECKED_BOX,
	'[*]': CHECKED_BOX,
	'*': BULLET,
}

# reverse dict
bullet_types = {}
for bullet in bullets:
	bullet_types[bullets[bullet]] = bullet

bullet_pattern = u'(?:[\\*\u2022]|\\[[ \\*x]\\]|\\d+\\.|\\w\\.)[\\ \\t]+'
	# bullets can be '*' or 0x2022 for normal items
	# and '[ ]', '[*]' or '[x]' for checkbox items
	# and '1.', '10.', or 'a.' for numbered items (but not 'aa.')

bullet_line_re = re.compile(r'^(\t*)(%s)(.*\n)$' % bullet_pattern)
	# matches list item: prefix, bullet, text

number_bullet_re = re.compile('^(\d+|\w)\.$')
def check_number_bullet(bullet):
	'''If bullet is a numbered bullet this returns the number or letter,
	C{None} otherwise
	'''
	m = number_bullet_re.match(bullet)
	if m:
		return m.group(1)
	else:
		return None

param_re = re.compile('(\w+)\s*\=\s*"((?:[^"]|"{2})*)"')
	# matches parameter list for objects

empty_lines_re = re.compile(r'((?:^[\ \t]*\n)+)', re.M | re.U)
	# match multiple empty lines

unindented_line_re = re.compile('^\S', re.M)
	# match any unindented line


class WikiParser(object):
	# This parser uses 3 levels of rules. The top level splits up
	# paragraphs, verbatim paragraphs, images and objects.
	# The second level further splits paragraphs in lists and indented
	# blocks. The third level does the inline formatting for all
	# text.

	def __init__(self):
		self.inline_parser = self._init_inline_parse()
		self.list_and_indent_parser = self._init_intermediate_parser()
		self.block_parser = self._init_block_parser()

	def __call__(self, builder, text):
		builder.start(FORMATTEDTEXT)
		self.block_parser(builder, text)
		builder.end(FORMATTEDTEXT)

	def _init_inline_parse(self):
		# Rules for inline formatting, links and tags
		return (
			Rule(LINK, url_re.r, process=self.parse_url) # FIXME need .r atribute because url_re is a Re object
			| Rule(TAG, r'(?<!\S)@\w+', process=self.parse_tag)
			| Rule(LINK, r'\[\[(?!\[)(.*?)\]\]', process=self.parse_link)
			| Rule(IMAGE, r'\{\{(?!\{)(.*?)\}\}', process=self.parse_image)
			| Rule(EMPHASIS, r'//(?!/)(.*?)//')
			| Rule(STRONG, r'\*\*(?!\*)(.*?)\*\*')
			| Rule(MARK, r'__(?!_)(.*?)__')
			| Rule(SUBSCRIPT, r'_\{(?!~)(.+?)\}')
			| Rule(SUPERSCRIPT, r'\^\{(?!~)(.+?)\}')
			| Rule(STRIKE, r'~~(?!~)(.+?)~~')
			| Rule(VERBATIM, r"''(?!')(.+?)''")
		)

	def _init_intermediate_parser(self):
		# Intermediate level, breaks up lists and indented blocks
		# TODO: deprecate this by taking lists out of the para
		#       and make a new para for each indented block
		p = Parser(
			Rule(
				'X-Bullet-List',
				r'(^%s.*\n(?:^\t*%s.*\n)*)' % (bullet_pattern, bullet_pattern),
				process=self.parse_list
			),
			Rule(
				'X-Indented-Bullet-List',
				r'(^(?P<list_indent>\t+)%s.*\n(?:^(?P=list_indent)\t*%s.*\n)*)' % (bullet_pattern, bullet_pattern),
				process=self.parse_list
			),
			Rule(
				'X-Indented-Block',
				r'(^(?P<block_indent>\t+).*\n(?:^(?P=block_indent)(?!\t).*\n)*)',
				process=self.parse_indent
			),
		)
		p.process_unmatched = self.inline_parser
		return p

	def _init_block_parser(self):
		# Top level parser, to break up block level items
		p = Parser(
			Rule(VERBATIM_BLOCK, r'''
				^(?P<pre_indent>\t*) \'\'\' \s*?				# 3 "'"
				( (?:^.*\n)*? )									# multi-line text
				^(?P=pre_indent) \'\'\' \s*? \n					# another 3 "'" with matching indent
				''',
				process=self.parse_pre
			),
			Rule(OBJECT, r'''
				^(?P<obj_indent>\t*) \{\{\{ \s*? (\S+:.*\n)		# "{{{ object_type: attrib=..."
				( (?:^.*\n)*? ) 								# multi-line body
				^(?P=obj_indent) \}\}\} \s*? \n					# "}}}" with matching indent
				''',
				process=self.parse_object
			),
			Rule(HEADING,
				r'^( ==+ [\ \t]+ \S.*? ) [\ \t]* =* \n', # "==== heading ===="
				process=self.parse_heading
			),
		)
		p.process_unmatched = self.parse_para
		return p

	@staticmethod
	def parse_heading(builder, text):
		'''Parse heading and determine it's level'''
		assert text.startswith('=')
		for i, c in enumerate(text):
			if c != '=':
				break

		level = 7 - min(6, i)
			# == is level 5
			# === is level 4
			# ...
			# ======= is level 1

		text = text[i:].lstrip() + '\n'
		builder.append(HEADING, {'level': level}, text)

	@staticmethod
	def parse_pre(builder, indent, text):
		'''Verbatim block with indenting'''
		if indent:
			text = re.sub('^'+indent, '', text, flags=re.M) # remove indent
			attrib = {'indent': len(indent)}
		else:
			attrib = None

		builder.append(VERBATIM_BLOCK, attrib, text)

	@staticmethod
	def parse_object(builder, indent, header, body):
		'''Custom object'''
		type, param = header.split(':', 1)
		type = type.strip().lower()

		attrib = {}
		for match in param_re.finditer(param):
			key = match.group(1).lower()
			value = match.group(2).replace('""', '"')
			attrib[key] = value

		# Defined after parsing head, so these attrib can not be overruled
		# accidentally
		attrib['type'] = type
		if indent:
			body = re.sub('^'+indent, '', body, flags=re.M) # remove indent
			attrib['indent'] = len(indent)

		builder.append(OBJECT, attrib, body)

	def parse_para(self, builder, text):
		'''Split a text into paragraphs and empty lines'''
		if text.isspace():
			builder.text(text)
		else:
			for block in empty_lines_re.split(text):
				if not block: # empty string due to split
					pass
				elif block.isspace():
					builder.text(block)
				elif self.backward \
				and not unindented_line_re.search(block):
					# Before zim 0.29 all indented paragraphs were
					# verbatim.
					builder.append(VERBATIM_BLOCK, None, block)
				else:
					builder.start(PARAGRAPH)
					self.list_and_indent_parser(builder, block)
					builder.end(PARAGRAPH)

	def parse_list(self, builder, text, indent=None):
		'''Parse lists into items and recurse to get inline formatting
		per list item
		'''
		if indent:
			text = re.sub('^'+indent, '', text, flags=re.M) # remove indent
			attrib = {'indent': len(indent)}
		else:
			attrib = None

		lines = text.splitlines(True)
		self.parse_list_lines(builder, lines, 0, attrib)

	def parse_list_lines(self, builder, lines, level, attrib=None):
		listtype = None
		first = True
		while lines:
			line = lines[0]
			m = bullet_line_re.match(line)
			assert m, 'Line does not match a list item: >>%s<<' % line
			prefix, bullet, text = m.groups()
			bullet = bullet.rstrip()

			if first:
				number = check_number_bullet(bullet)
				if number:
					listtype = NUMBEREDLIST
					if not attrib:
						attrib = {}
					attrib['start'] = number
				else:
					listtype = BULLETLIST
				builder.start(listtype, attrib)
				first = False

			mylevel = len(prefix)
			if mylevel > level:
				self.parse_list_lines(builder, lines, level+1) # recurs
			elif mylevel < level:
				builder.end(listtype)
				return
			else:
				if listtype == NUMBEREDLIST:
					attrib = None
				elif bullet in bullets: # BULLETLIST
					attrib = {'bullet': bullets[bullet]}
				else: # BULLETLIST
					attrib = {'bullet': BULLET}
				builder.start(LISTITEM, attrib)
				self.inline_parser(builder, text)
				builder.end(LISTITEM)

				lines.pop(0)

		builder.end(listtype)

	def parse_indent(self, builder, text, indent):
		'''Parse indented blocks and turn them into 'div' elements'''
		text = re.sub('^'+indent, '', text, flags=re.M) # remove indent
		builder.start(BLOCK, {'indent': len(indent)})
		self.inline_parser(builder, text)
		builder.end(BLOCK)

	@staticmethod
	def parse_link(builder, text):
		if '|' in text:
			href, text = text.split('|', 1)
			if not href: # old bug producing "[[|link]]"
				href = text
		else:
			href, text = text, text

		builder.append(LINK, {'href': href}, text)

	@staticmethod
	def parse_image(builder, text):
		if '|' in text:
			url, text = text.split('|', 1)
		else:
			url, text = text, None

		attrib = ParserClass.parse_image_url(url)
		if text:
			attrib['alt'] = text

		builder.append(IMAGE, attrib)

	@staticmethod
	def parse_url(builder, text):
		builder.append(LINK, {'href': text}, text)

	@staticmethod
	def parse_tag(builder, text):
		builder.append(TAG, {'name': text[1:]}, text)




wikiparser = WikiParser() #: singleton instance


# FIXME FIXME we are redefining Parser here !
class Parser(ParserClass):

	def __init__(self, version=WIKI_FORMAT_VERSION):
		self.backward = version not in ('zim 0.26', WIKI_FORMAT_VERSION)

	def parse(self, input):
		if not isinstance(input, basestring):
			input = ''.join(input)

		input = prepare_text(input)

		builder = ParseTreeBuilder()
		wikiparser.backward = self.backward # HACK
		wikiparser(builder, input)
		return builder.get_parsetree()


class Dumper(DumperClass):

	TAGS = {
		EMPHASIS:		('//', '//'),
		STRONG:			('**', '**'),
		MARK:			('__', '__'),
		STRIKE:			('~~', '~~'),
		VERBATIM:		("''", "''"),
		TAG:			('', ''), # No additional annotation (apart from the visible @)
		SUBSCRIPT:		('_{', '}'),
		SUPERSCRIPT:	('^{', '}'),
	}

	# TODO check commonality with dumper in plain.py

	def __init__(self, *arg, **kwarg):
		self._list_level = -1
		self._list_type = None
		self._list_iter = None
		DumperClass.__init__(self, *arg, **kwarg)

	def dump(self, tree):
		# FIXME reset here
		self._list_level = -1
		self._list_type = None
		self._list_iter = None
		return DumperClass.dump(self, tree)

	def accept_indent(self, tag, attrib=None):
		if attrib and 'indent' in attrib:
			prefix = '\t' * int(attrib['indent'])
			block = self.__class__(self.linker, self.template_options)
			yield block
			self._text += [prefix + l for l in block.get_lines()]
			# TODO enforces we always and such a block with \n unless partial
		else:
			yield self

	accept_p = accept_indent
	accept_div = accept_indent

	def accept_pre(self, tag, attrib):
		for block in self.accept_indent(tag, attrib):
			block.text("'''\n")
			yield block
			block.text("'''\n")

	def accept_h(self, tag, attrib):
		level = int(attrib['level'])
		if level < 1:   level = 1
		elif level > 5: level = 5
		tag = '='*(7 - level)
		self._text.append(tag + ' ')
		yield self
		self._text.append(' ' + tag)

	def accept_list(self, tag, attrib):
		parent = (self._list_level, self._list_type, self._list_iter)
		if 'indent' in attrib:
			self._list_level = int(attrib['indent'])
		else:
			self._list_level += 1
		self._list_type = tag
		self._list_iter = attrib.get('start')

		yield self

		self._list_level = parent[0]
		self._list_type = parent[1]
		self._list_iter = parent[2]

	accept_ul = accept_list
	accept_ol = accept_list

	def accept_li(self, tag, attrib):
		if 'indent' in attrib:
			# HACK for raw trees from pageview
			self._list_level = int(attrib['indent'])

		if 'bullet' in attrib: # ul OR raw tree from pageview...
			if attrib['bullet'] in bullet_types:
				bullet = bullet_types[attrib['bullet']]
			else:
				bullet = attrib['bullet'] # Assume it is numbered..
		elif self._list_type == NUMBEREDLIST:
			bullet = str(self._list_iter) + '.'
			self._list_iter = increase_list_iter(self._list_iter) or '1' # fallback if iter not valid
		else: # BULLETLIST
			bullet = '*'

		self._text.append('\t'*self._list_level+bullet+' ')
		yield self
		self._text.append('\n')

		# TODO will also need to use accept_indent here when we allow
		# para in list items

	def append(self, tag, attrib, text):
		if tag == LINK:
			assert 'href' in attrib, \
				'BUG: link misses href: %s "%s"' % (attrib, text)
			href = attrib['href']
			if href == text:
				if url_re.match(href):
					self._text.append(href)
				else:
					self._text.append('[['+href+']]')
			else:
				if text:
					self._text.append('[['+href+'|'+text+']]')
				else:
					self._text.append('[['+href+']]')
		elif tag == IMAGE:
			src = attrib['src']
			alt = attrib.get('alt')
			opts = []
			items = attrib.items()
			# we sort params only because unit tests don't like random output
			items.sort()
			for k, v in items:
				if k in ('src', 'alt') or k.startswith('_'):
					continue
				elif v: # skip None, "" and 0
					opts.append('%s=%s' % (k, v))
			if opts:
				src += '?%s' % '&'.join(opts)

			if alt:
				self._text.append('{{'+src+'|'+alt+'}}')
			else:
				self._text.append('{{'+src+'}}')

			# TODO use text for caption (with full recursion)
			# means moving to an accept_img method
		elif tag == OBJECT:
			logger.debug("Exporting object: %s, %s", attrib, text)
			assert "type" in attrib, "Undefined type of object"
			self._text.append("{{{" + attrib["type"] + ":")
			for key, value in attrib.items():
				if key in ('type', 'indent') or not value:
					continue
				# double quotes are escaped by doubling them
				self._text.append(' %s="%s"' % (key, str(value).replace('"', '""')))
			self._text.append("\n" + (text or '') + "}}}\n")

			# TODO put content in attrib, use text for caption (with full recursion)
			# See img
		else:
			DumperClass.append(self, tag, attrib, text)
