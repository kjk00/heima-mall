-- MySQL 8.0.16+ / InnoDB. Public timestamps retain the API ISO-8601 format.

CREATE TABLE IF NOT EXISTS mall_transaction_guard (id BIGINT PRIMARY KEY) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `catalog_cache_state` (
    `id` BIGINT NOT NULL,
    `generation` VARCHAR(32) NOT NULL,
    `revision` BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `product_sync_outbox` (
    `seq` BIGINT NOT NULL AUTO_INCREMENT,
    `product_id` BIGINT NOT NULL,
    PRIMARY KEY (`seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `stores` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(200) COLLATE utf8mb4_0900_as_cs NOT NULL,
    `description` TEXT NOT NULL DEFAULT (''),
    `phone` VARCHAR(30) NOT NULL DEFAULT '',
    `is_active` BIGINT NOT NULL DEFAULT 1,
    `shipping_fee` BIGINT NOT NULL DEFAULT 1000,
    `free_shipping_threshold` BIGINT NOT NULL DEFAULT 19900,
    `version` BIGINT NOT NULL DEFAULT 1,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`id`),
    UNIQUE KEY `stores_unique_0` (`name`),
    CHECK (is_active IN (0,1)),
    CHECK (shipping_fee>=0),
    CHECK (free_shipping_threshold>=0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `users` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `phone` VARCHAR(11) NOT NULL,
    `name` VARCHAR(200) NOT NULL,
    `password_hash` VARCHAR(256) NOT NULL,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    `role` VARCHAR(16) NOT NULL DEFAULT 'customer',
    `is_active` BIGINT NOT NULL DEFAULT 1,
    `notify_support` BIGINT NOT NULL DEFAULT 1,
    `store_id` BIGINT,
    `is_super_admin` BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `users_unique_0` (`phone`),
    FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`),
    CHECK (role IN ('customer','admin')),
    CHECK (is_active IN (0,1)),
    CHECK (is_super_admin IN (0,1)),
    CHECK (notify_support IN (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `sessions` (
    `token_hash` VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `user_id` BIGINT NOT NULL,
    `expires_at` BIGINT NOT NULL,
    PRIMARY KEY (`token_hash`),
    KEY `sessions_user` (`user_id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `login_captchas` (
    `id` VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `code` VARCHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `expires_at` BIGINT NOT NULL,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `products` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(200) NOT NULL,
    `subtitle` VARCHAR(300) NOT NULL,
    `category` VARCHAR(30) NOT NULL,
    `price` BIGINT NOT NULL,
    `original_price` BIGINT NOT NULL,
    `stock` BIGINT NOT NULL,
    `image` VARCHAR(400) NOT NULL,
    `badge` VARCHAR(50) NOT NULL DEFAULT '',
    `color` VARCHAR(20) NOT NULL,
    `description` TEXT NOT NULL,
    `detail_html` TEXT NOT NULL DEFAULT (''),
    `params_json` TEXT NOT NULL DEFAULT ('[]'),
    `sales` BIGINT NOT NULL DEFAULT 0,
    `is_active` BIGINT NOT NULL DEFAULT 1,
    `version` BIGINT NOT NULL DEFAULT 1,
    `store_id` BIGINT,
    PRIMARY KEY (`id`),
    KEY `products_store` (`store_id`,`is_active`,`id`),
    FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`),
    CHECK (price>=0),
    CHECK (stock>=0),
    CHECK (is_active IN (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `cart_items` (
    `user_id` BIGINT NOT NULL,
    `product_id` BIGINT NOT NULL,
    `quantity` BIGINT NOT NULL,
    PRIMARY KEY (`user_id`,`product_id`),
    FOREIGN KEY (`product_id`) REFERENCES `products` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
    CHECK (quantity BETWEEN 1 AND 99)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `addresses` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT NOT NULL,
    `recipient` VARCHAR(30) NOT NULL,
    `phone` VARCHAR(11) NOT NULL,
    `address` VARCHAR(200) NOT NULL,
    `label` VARCHAR(20) NOT NULL DEFAULT '',
    `is_default` BIGINT NOT NULL DEFAULT 0,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    `default_owner` BIGINT GENERATED ALWAYS AS (CASE WHEN is_default=1 THEN user_id ELSE NULL END) STORED,
    PRIMARY KEY (`id`),
    UNIQUE KEY `address_default` (`default_owner`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `coupons` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `code` VARCHAR(60) NOT NULL,
    `name` VARCHAR(200) NOT NULL,
    `kind` VARCHAR(16) NOT NULL,
    `value` BIGINT NOT NULL,
    `threshold` BIGINT NOT NULL DEFAULT 0,
    `max_discount` BIGINT NOT NULL DEFAULT 0,
    `claim_limit` BIGINT NOT NULL DEFAULT 200,
    `starts_at` VARCHAR(20) NOT NULL,
    `expires_at` VARCHAR(20) NOT NULL,
    `is_active` BIGINT NOT NULL DEFAULT 1,
    `store_id` BIGINT,
    PRIMARY KEY (`id`),
    UNIQUE KEY `coupons_unique_0` (`code`),
    FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`),
    CHECK (kind IN ('amount','rate'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `orders` (
    `id` VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `user_id` BIGINT NOT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
    `subtotal` BIGINT NOT NULL,
    `shipping_fee` BIGINT NOT NULL,
    `total` BIGINT NOT NULL,
    `recipient` VARCHAR(30) NOT NULL,
    `phone` VARCHAR(11) NOT NULL,
    `address` VARCHAR(200) NOT NULL,
    `idempotency_key` VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `request_hash` VARCHAR(64) NOT NULL DEFAULT '',
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    `fulfillment_status` VARCHAR(20) NOT NULL DEFAULT 'unfulfilled',
    `carrier` VARCHAR(80) NOT NULL DEFAULT '',
    `tracking_no` VARCHAR(100) NOT NULL DEFAULT '',
    `shipped_at` VARCHAR(20),
    `completed_at` VARCHAR(20),
    `delivery_stage` VARCHAR(20) NOT NULL DEFAULT 'awaiting',
    `discount` BIGINT NOT NULL DEFAULT 0,
    `coupon_id` BIGINT,
    `store_id` BIGINT,
    `store_name` VARCHAR(200) NOT NULL DEFAULT '黑马自营店',
    `created_seq` BIGINT NOT NULL AUTO_INCREMENT UNIQUE,
    PRIMARY KEY (`id`),
    KEY `orders_store` (`store_id`,`created_at`),
    KEY `orders_user` (`user_id`,`created_at`),
    KEY `orders_expiry` (`status`,`created_at`),
    UNIQUE KEY `orders_unique_2` (`user_id`,`idempotency_key`),
    FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`),
    FOREIGN KEY (`coupon_id`) REFERENCES `coupons` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
    CHECK (status IN ('pending','paid','cancelled')),
    CHECK (fulfillment_status IN ('unfulfilled','shipped','completed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `order_items` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `order_id` VARCHAR(200) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `product_id` BIGINT NOT NULL,
    `name` VARCHAR(200) NOT NULL,
    `image` VARCHAR(400) NOT NULL,
    `price` BIGINT NOT NULL,
    `quantity` BIGINT NOT NULL,
    PRIMARY KEY (`id`),
    FOREIGN KEY (`product_id`) REFERENCES `products` (`id`),
    FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `user_coupons` (
    `user_id` BIGINT NOT NULL,
    `coupon_id` BIGINT NOT NULL,
    `used_order_id` VARCHAR(200) CHARACTER SET ascii COLLATE ascii_bin,
    `claimed_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`user_id`,`coupon_id`),
    FOREIGN KEY (`used_order_id`) REFERENCES `orders` (`id`),
    FOREIGN KEY (`coupon_id`) REFERENCES `coupons` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `refunds` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `order_id` VARCHAR(200) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `user_id` BIGINT NOT NULL,
    `reason` TEXT NOT NULL,
    `amount` BIGINT NOT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
    `idempotency_key` VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `note` TEXT NOT NULL DEFAULT (''),
    `restocked` BIGINT NOT NULL DEFAULT 0,
    `processed_by` BIGINT,
    `processed_at` VARCHAR(20),
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    `active_order` VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (CASE WHEN status IN ('pending','approved') THEN order_id ELSE NULL END) STORED,
    PRIMARY KEY (`id`),
    UNIQUE KEY `refund_open_order` (`active_order`),
    UNIQUE KEY `refunds_unique_1` (`user_id`,`idempotency_key`),
    FOREIGN KEY (`processed_by`) REFERENCES `users` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
    FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`),
    CHECK (status IN ('pending','approved','rejected'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `tracking_events` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `order_id` VARCHAR(200) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `stage` VARCHAR(200) NOT NULL,
    `note` TEXT NOT NULL,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`id`),
    FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `browsing_history` (
    `user_id` BIGINT NOT NULL,
    `product_id` BIGINT NOT NULL,
    `viewed_at` VARCHAR(20) NOT NULL,
    `sort_key` BIGINT NOT NULL,
    PRIMARY KEY (`user_id`,`product_id`),
    FOREIGN KEY (`product_id`) REFERENCES `products` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `reviews` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT NOT NULL,
    `product_id` BIGINT NOT NULL,
    `order_id` VARCHAR(200) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `rating` BIGINT NOT NULL,
    `content` TEXT NOT NULL,
    `is_visible` BIGINT NOT NULL DEFAULT 1,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    `updated_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`id`),
    KEY `review_product` (`product_id`,`is_visible`,`id`),
    UNIQUE KEY `reviews_unique_1` (`user_id`,`product_id`),
    FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`),
    FOREIGN KEY (`product_id`) REFERENCES `products` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
    CHECK (rating BETWEEN 1 AND 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `comments` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT NOT NULL,
    `product_id` BIGINT NOT NULL,
    `content` TEXT NOT NULL,
    `is_visible` BIGINT NOT NULL DEFAULT 1,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`id`),
    KEY `comment_product` (`product_id`,`is_visible`,`id`),
    FOREIGN KEY (`product_id`) REFERENCES `products` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `conversations` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT NOT NULL,
    `store_id` BIGINT NOT NULL,
    `customer_read_id` BIGINT NOT NULL DEFAULT 0,
    `staff_read_id` BIGINT NOT NULL DEFAULT 0,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`id`),
    UNIQUE KEY `conversations_unique_0` (`user_id`,`store_id`),
    FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`),
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `support_messages` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `conversation_id` BIGINT NOT NULL,
    `sender_id` BIGINT NOT NULL,
    `sender_side` VARCHAR(16) NOT NULL,
    `content` TEXT NOT NULL,
    `client_message_id` VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `created_at` VARCHAR(20) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')),
    PRIMARY KEY (`id`),
    KEY `support_thread` (`conversation_id`,`id`),
    UNIQUE KEY `support_messages_unique_1` (`conversation_id`,`sender_id`,`sender_side`,`client_message_id`),
    FOREIGN KEY (`sender_id`) REFERENCES `users` (`id`),
    FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`),
    CHECK (sender_side IN ('customer','staff'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
