<?php

$config = array();

// Veritabanı bağlantısı
$config['db_dsnw'] = 'mysql://roundcube:sys6790abc@localhost/roundcubemail';

// Mail sunucusu (IMAP)
$config['default_host'] = 'localhost';

// SMTP sunucusu
$config['smtp_server'] = 'localhost';
$config['smtp_port'] = 587;
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

// Şifreleme anahtarı (Rastgele oluşturulmalı, değiştirilebilir)
$config['des_key'] = 'kRbnxMail2025KeySecret!';

// Ürün bilgisi
$config['product_name'] = 'Karbonex Mail';

// Destek URL'si (isteğe bağlı)
$config['support_url'] = '';

// Kurulum ekranını devre dışı bırak
$config['enable_installer'] = false;

// Eklentiler
$config['plugins'] = array(
  'archive',
  'zipdownload',
  'managesieve',
  'password'
);

// Charset
$config['default_charset'] = 'UTF-8';

?>
