import scrapy

class JailBookingItem(scrapy.Item):
    county = scrapy.Field()
    inmate_name = scrapy.Field()
    booking_date = scrapy.Field()
    charges = scrapy.Field()
    bond = scrapy.Field()
    source_url = scrapy.Field()
